import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from adapters.browser.playwright_actions import PlaywrightActionExecutor
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.conversation_config import get_conversation_policy
from apps.api.app.models import entities as db
from apps.api.app.schemas.action import ActionResponse
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.actions import ActionExecutor, ApprovedCommand, ExecutionResult
from packages.policy_engine.content_check import validate_edited_content
from packages.policy_engine.state_machine import ActionStatus, require_transition


def create_resume_confirmation(session: Session, conversation_id: UUID, resume_id: UUID) -> UUID:
    conversation, job = _conversation_job(session, conversation_id)
    resume = session.get(db.Resume, resume_id)
    if resume is None or resume.user_id != DEFAULT_USER_ID or not resume.is_available:
        raise ValueError("简历附件不存在或不可用")
    if resume.platform != conversation.platform:
        raise ValueError("简历平台与对话平台不匹配")
    _require_eligible_score(session, job.id)
    if session.scalar(
        select(db.ResumeSendRecord).where(
            db.ResumeSendRecord.conversation_id == conversation.id,
            db.ResumeSendRecord.resume_id == resume.id,
        )
    ):
        raise ValueError("当前对话已发送过该简历")
    fingerprint = hashlib.sha256(f"RESUME:{conversation.id}:{resume.id}".encode()).hexdigest()
    draft = session.scalar(
        select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
    )
    if draft:
        decision = session.scalar(
            select(db.PolicyDecision).where(db.PolicyDecision.draft_id == draft.id)
        )
        task = (
            session.scalar(
                select(db.ConfirmationTask).where(db.ConfirmationTask.decision_id == decision.id)
            )
            if decision
            else None
        )
        if task:
            return task.id
    draft = db.GeneratedDraft(
        user_id=DEFAULT_USER_ID,
        conversation_id=conversation.id,
        draft_type="RESUME",
        content=resume.attachment_name,
        intents=["RESUME_REQUEST"],
        fact_ids=[],
        confidence=1,
        risk_codes=[],
        input_fingerprint=fingerprint,
        generator_version="resume-confirmation-v1",
    )
    session.add(draft)
    session.flush()
    decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=draft.id,
        action_type="RESUME",
        decision="REQUIRE_CONFIRMATION",
        reason_codes=["RESUME_SEND_REQUIRES_APPROVAL"],
        policy_version="manual-action-v1",
        input_snapshot={"resume_id": str(resume.id)},
    )
    session.add(decision)
    session.flush()
    task = db.ConfirmationTask(
        user_id=DEFAULT_USER_ID,
        decision_id=decision.id,
        expires_at=_confirmation_expiry(),
    )
    session.add(task)
    _audit(
        session,
        "CONFIRMATION_CREATED",
        "confirmation_task",
        task.id,
        None,
        "PENDING_APPROVAL",
        ["RESUME_SEND_REQUIRES_APPROVAL"],
    )
    session.commit()
    return task.id


def create_greeting_confirmation(
    session: Session,
    draft_id: UUID,
    recruiter_name: str,
) -> UUID:
    draft = session.get(db.GeneratedDraft, draft_id)
    if (
        draft is None
        or draft.user_id != DEFAULT_USER_ID
        or draft.draft_type != "GREETING"
        or draft.job_score_id is None
    ):
        raise ResourceNotFoundError("招呼语草稿不存在")
    score, job = _require_greeting_score(session, draft.job_score_id)
    existing = session.scalar(
        select(db.ConfirmationTask)
        .join(db.PolicyDecision, db.PolicyDecision.id == db.ConfirmationTask.decision_id)
        .where(
            db.PolicyDecision.draft_id == draft.id,
            db.PolicyDecision.action_type == "GREETING",
            db.PolicyDecision.policy_version == "manual-live-greeting-v1",
        )
    )
    if existing:
        return existing.id
    decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=draft.id,
        action_type="GREETING",
        decision="REQUIRE_CONFIRMATION",
        reason_codes=["FIRST_LIVE_GREETING_REQUIRES_APPROVAL"],
        policy_version="manual-live-greeting-v1",
        input_snapshot={
            "job_id": str(job.id),
            "job_score_id": str(score.id),
            "recruiter_name": recruiter_name.strip(),
        },
    )
    session.add(decision)
    session.flush()
    task = db.ConfirmationTask(
        user_id=DEFAULT_USER_ID,
        decision_id=decision.id,
        expires_at=_confirmation_expiry(),
    )
    session.add(task)
    session.flush()
    _audit(
        session,
        "CONFIRMATION_CREATED",
        "confirmation_task",
        task.id,
        None,
        "PENDING_APPROVAL",
        ["FIRST_LIVE_GREETING_REQUIRES_APPROVAL"],
    )
    session.commit()
    return task.id


def approve_task(
    session: Session,
    task_id: UUID,
    conversation_id: UUID | None,
    idempotency_key: str,
) -> ActionResponse:
    existing = session.scalar(
        select(db.ActionQueue).where(db.ActionQueue.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.confirmation_task_id != task_id:
            raise ValueError("幂等键已用于其他确认任务")
        return _response(existing)
    task, decision, draft = _task_bundle(session, task_id)
    if task.expires_at and task.expires_at < datetime.now(UTC):
        task.status = ActionStatus.EXPIRED.value
        session.commit()
        raise ValueError("确认任务已过期")
    require_transition(task.status, ActionStatus.APPROVED)
    conversation = None
    score = None
    if decision.action_type == "GREETING":
        if draft.job_score_id is None:
            raise ValueError("招呼语草稿缺少评分")
        score, job = _require_greeting_score(session, draft.job_score_id)
        if conversation_id is not None:
            raise ValueError("首次招呼尚未建立对话")
    else:
        if conversation_id is None:
            raise ValueError("当前动作必须指定对话")
        conversation, job = _conversation_job(session, conversation_id)
        if draft.conversation_id and draft.conversation_id != conversation.id:
            raise ValueError("草稿与对话不匹配")
    resume = None
    if decision.action_type == "RESUME":
        resume_id = UUID(str(decision.input_snapshot["resume_id"]))
        resume = session.get(db.Resume, resume_id)
        if resume is None or not resume.is_available:
            raise ValueError("简历附件不可用")
        _require_eligible_score(session, job.id)
    send_fingerprint = hashlib.sha256(
        (
            f"{conversation.id if conversation else job.id}:{decision.action_type}:"
            f"{draft.content}:{resume.id if resume else ''}"
        ).encode()
    ).hexdigest()
    duplicate = session.scalar(
        select(db.ActionQueue).where(db.ActionQueue.send_fingerprint == send_fingerprint)
    )
    if duplicate:
        raise ValueError("相同对话中已存在相同发送动作")
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID,
        confirmation_task_id=task.id,
        policy_decision_id=decision.id,
        strategy_id=score.strategy_id if score else None,
        job_id=job.id,
        conversation_id=conversation.id if conversation else None,
        draft_id=draft.id,
        resume_id=resume.id if resume else None,
        action_type=decision.action_type,
        status=ActionStatus.APPROVED.value,
        content=draft.content if decision.action_type != "RESUME" else None,
        platform=conversation.platform if conversation else job.source,
        target_company=job.company_name,
        target_job_title=job.title,
        target_recruiter=(
            conversation.recruiter_name
            if conversation
            else str(decision.input_snapshot["recruiter_name"])
        ),
        target_conversation_key=(
            conversation.external_conversation_id if conversation else None
        ),
        attachment_name=resume.attachment_name if resume else None,
        idempotency_key=idempotency_key,
        send_fingerprint=send_fingerprint,
        approved_at=datetime.now(UTC),
    )
    task.status = ActionStatus.APPROVED.value
    session.add(action)
    session.flush()
    _audit(session, "ACTION_APPROVED", "action", action.id, "PENDING_APPROVAL", "APPROVED", [])
    session.commit()
    session.refresh(action)
    return _response(action)


def modify_task(session: Session, task_id: UUID, content: str) -> UUID:
    task, decision, draft = _task_bundle(session, task_id)
    require_transition(task.status, ActionStatus.SUPERSEDED)
    risks = validate_edited_content(content)
    if risks:
        raise ValueError("修改内容未通过敏感信息检查")
    fingerprint = hashlib.sha256(f"EDIT:{draft.id}:{content}".encode()).hexdigest()
    new_draft = db.GeneratedDraft(
        user_id=DEFAULT_USER_ID,
        conversation_id=draft.conversation_id,
        message_id=draft.message_id,
        job_score_id=draft.job_score_id,
        draft_type=draft.draft_type,
        content=content,
        intents=draft.intents,
        fact_ids=draft.fact_ids,
        confidence=draft.confidence,
        risk_codes=[],
        input_fingerprint=fingerprint,
        generator_version="manual-edit-v1",
    )
    session.add(new_draft)
    session.flush()
    new_decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=new_draft.id,
        action_type=decision.action_type,
        decision="REQUIRE_CONFIRMATION",
        reason_codes=["USER_EDIT_RECHECKED"],
        policy_version="manual-action-v1",
        input_snapshot={"source_draft_id": str(draft.id)},
    )
    session.add(new_decision)
    session.flush()
    new_task = db.ConfirmationTask(
        user_id=DEFAULT_USER_ID,
        decision_id=new_decision.id,
        expires_at=_confirmation_expiry(),
    )
    task.status = ActionStatus.SUPERSEDED.value
    session.add(new_task)
    _audit(
        session,
        "CONFIRMATION_SUPERSEDED",
        "confirmation_task",
        task.id,
        "PENDING_APPROVAL",
        "SUPERSEDED",
        ["USER_EDIT"],
    )
    session.commit()
    return new_task.id


def reject_task(session: Session, task_id: UUID) -> None:
    task, _, _ = _task_bundle(session, task_id)
    require_transition(task.status, ActionStatus.CANCELLED)
    task.status = ActionStatus.CANCELLED.value
    _audit(
        session,
        "CONFIRMATION_REJECTED",
        "confirmation_task",
        task.id,
        "PENDING_APPROVAL",
        "CANCELLED",
        ["USER_REJECTED"],
    )
    session.commit()


def _confirmation_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=get_conversation_policy().confirmation_ttl_hours)


def execute_action(
    session: Session,
    action_id: UUID,
    cdp_url: str,
    executor: ActionExecutor | None = None,
) -> ActionResponse:
    action = _get_action(session, action_id)
    if action.status == ActionStatus.SUCCEEDED.value:
        return _response(action)
    if action.status == ActionStatus.OUTCOME_UNKNOWN.value:
        raise ValueError("结果不明确的动作必须先对账，不得重试")
    claimed_id = session.scalar(
        update(db.ActionQueue)
        .where(db.ActionQueue.id == action.id, db.ActionQueue.status == ActionStatus.APPROVED.value)
        .values(
            status=ActionStatus.EXECUTING.value,
            started_at=datetime.now(UTC),
            version=db.ActionQueue.version + 1,
        )
        .returning(db.ActionQueue.id)
    )
    if claimed_id is None:
        raise ValueError("动作未批准或已被其他执行者占用")
    attempt_number = (
        session.scalar(select(func.count()).where(db.ActionAttempt.action_id == action.id)) or 0
    ) + 1
    attempt = db.ActionAttempt(
        action_id=action.id,
        attempt_number=attempt_number,
        status="EXECUTING",
        started_at=datetime.now(UTC),
    )
    session.add(attempt)
    session.commit()
    session.refresh(action)
    job = session.get(db.Job, action.job_id) if action.job_id else None
    command = ApprovedCommand(
        action_type=action.action_type,
        platform=action.platform,
        conversation_key=action.target_conversation_key,
        external_job_id=job.external_job_id if job else None,
        company=action.target_company,
        job_title=action.target_job_title,
        recruiter=action.target_recruiter,
        content=action.content,
        attachment_name=action.attachment_name,
    )
    result = (executor or PlaywrightActionExecutor(get_browser_selectors())).execute(
        cdp_url, command
    )
    _finish(session, action, attempt, result)
    return _response(action)


def approve_retry(session: Session, action_id: UUID) -> ActionResponse:
    action = _get_action(session, action_id)
    require_transition(action.status, ActionStatus.APPROVED)
    before = action.status
    action.status = ActionStatus.APPROVED.value
    action.failure_code = None
    action.version += 1
    _audit(session, "ACTION_RETRY_APPROVED", "action", action.id, before, "APPROVED", ["USER_RETRY"])
    session.commit()
    session.refresh(action)
    return _response(action)


def list_tasks(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(
        select(db.ConfirmationTask)
        .where(db.ConfirmationTask.user_id == DEFAULT_USER_ID)
        .order_by(db.ConfirmationTask.created_at.desc())
    ).all()
    result = []
    for task in rows:
        decision = session.get(db.PolicyDecision, task.decision_id)
        draft = session.get(db.GeneratedDraft, decision.draft_id) if decision else None
        conversation = session.get(db.Conversation, draft.conversation_id) if draft and draft.conversation_id else None
        job_id = conversation.job_id if conversation else None
        if job_id is None and draft and draft.job_score_id:
            score = session.get(db.JobScore, draft.job_score_id)
            job_id = score.job_id if score else None
        job = session.get(db.Job, job_id) if job_id else None
        result.append(
            {
                "id": task.id,
                "status": task.status,
                "action_type": decision.action_type if decision else None,
                "reason_codes": decision.reason_codes if decision else [],
                "content": draft.content if draft else None,
                "confidence": float(draft.confidence) if draft else None,
                "expires_at": task.expires_at,
                "platform": conversation.platform if conversation else (job.source if job else None),
                "company": job.company_name if job else None,
                "job_title": job.title if job else None,
                "recruiter": (
                    conversation.recruiter_name
                    if conversation
                    else (
                        decision.input_snapshot.get("recruiter_name")
                        if decision
                        else None
                    )
                ),
            }
        )
    return result


def _finish(
    session: Session, action: db.ActionQueue, attempt: db.ActionAttempt, result: ExecutionResult
) -> None:
    target = ActionStatus(result.outcome.value)
    require_transition(action.status, target)
    action.status = target.value
    action.failure_code = result.error_code
    action.finished_at = datetime.now(UTC)
    action.version += 1
    attempt.status = target.value
    attempt.error_code = result.error_code
    attempt.external_reference = result.external_reference
    attempt.evidence_hash = result.evidence_hash
    attempt.finished_at = datetime.now(UTC)
    if (
        target is ActionStatus.SUCCEEDED
        and action.action_type == "RESUME"
        and action.resume_id
        and action.conversation_id
    ):
        session.add(
            db.ResumeSendRecord(
                conversation_id=action.conversation_id,
                resume_id=action.resume_id,
                action_id=action.id,
                sent_at=datetime.now(UTC),
                external_reference=result.external_reference,
            )
        )
    _audit(
        session,
        "ACTION_EXECUTION_FINISHED",
        "action",
        action.id,
        "EXECUTING",
        target.value,
        [result.error_code] if result.error_code else [],
    )
    session.commit()
    session.refresh(action)


def _task_bundle(
    session: Session, task_id: UUID
) -> tuple[db.ConfirmationTask, db.PolicyDecision, db.GeneratedDraft]:
    task = session.get(db.ConfirmationTask, task_id)
    if task is None or task.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("确认任务不存在")
    decision = session.get(db.PolicyDecision, task.decision_id)
    draft = session.get(db.GeneratedDraft, decision.draft_id) if decision else None
    if decision is None or draft is None:
        raise RuntimeError("确认任务数据不完整")
    return task, decision, draft


def _conversation_job(session: Session, conversation_id: UUID) -> tuple[db.Conversation, db.Job]:
    conversation = session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    job = session.get(db.Job, conversation.job_id)
    if job is None:
        raise ResourceNotFoundError("职位不存在")
    return conversation, job


def _require_eligible_score(session: Session, job_id: UUID) -> None:
    score = session.scalar(
        select(db.JobScore)
        .where(db.JobScore.job_id == job_id)
        .order_by(db.JobScore.created_at.desc())
        .limit(1)
    )
    if (
        score is None
        or score.hard_rejected
        or score.effective_job_status != "OPEN"
        or score.total_score < 60
    ):
        raise ValueError("职位不满足简历发送条件")


def _require_greeting_score(
    session: Session,
    score_id: UUID,
) -> tuple[db.JobScore, db.Job]:
    score = session.get(db.JobScore, score_id)
    if score is None:
        raise ResourceNotFoundError("评分不存在")
    job = session.get(db.Job, score.job_id)
    strategy = session.get(db.JobStrategy, score.strategy_id)
    profile = session.get(db.CandidateProfile, score.candidate_profile_id)
    latest_score_id = session.scalar(
        select(db.JobScore.id)
        .where(
            db.JobScore.job_id == score.job_id,
            db.JobScore.strategy_id == score.strategy_id,
        )
        .order_by(db.JobScore.created_at.desc())
        .limit(1)
    )
    if (
        job is None
        or strategy is None
        or profile is None
        or latest_score_id != score.id
        or strategy.version != score.strategy_version
        or profile.version != score.profile_version
        or score.hard_rejected
        or score.effective_job_status != "OPEN"
        or score.total_score < 80
        or not score.llm_recommends_proactive_contact
        or not score.automation_eligible
    ):
        raise ValueError("职位不满足主动沟通条件或评分已过期")
    return score, job


def _get_action(session: Session, action_id: UUID) -> db.ActionQueue:
    action = session.get(db.ActionQueue, action_id)
    if action is None or action.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("动作不存在")
    return action


def _response(action: db.ActionQueue) -> ActionResponse:
    return ActionResponse(
        id=action.id,
        confirmation_task_id=action.confirmation_task_id,
        action_type=action.action_type,
        status=action.status,
        job_id=action.job_id,
        conversation_id=action.conversation_id,
        content=action.content,
        attachment_name=action.attachment_name,
        failure_code=action.failure_code,
        version=action.version,
    )


def _audit(
    session: Session,
    event: str,
    entity_type: str,
    entity_id: UUID,
    before: str | None,
    after: str | None,
    reasons: list[str],
) -> None:
    session.add(
        db.AuditEvent(
            user_id=DEFAULT_USER_ID,
            actor_type="USER" if "APPROV" in event or "REJECT" in event else "SYSTEM",
            event_type=event,
            entity_type=entity_type,
            entity_id=entity_id,
            before_state=before,
            after_state=after,
            reason_codes=reasons,
            metadata_json={},
            correlation_id=str(entity_id),
        )
    )
