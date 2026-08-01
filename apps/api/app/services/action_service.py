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
from packages.browser_worker.actions import (
    ActionExecutor,
    ApprovedCommand,
    ExecutionOutcome,
    ExecutionResult,
)
from packages.policy_engine.content_check import validate_edited_content
from packages.policy_engine.state_machine import ActionStatus, ActionType, require_transition

PREWRITE_RETRYABLE_FAILURES = {
    "APPROVED_TARGET_PAGE_NOT_FOUND",
    "GREETING_TRIGGER_NOT_VISIBLE",
    "COMPOSER_FILL_NOT_CONFIRMED",
    "SEND_BUTTON_NOT_READY",
    "RESUME_TRIGGER_NOT_READY",
    "RESUME_CONFIRM_NOT_READY",
    "PLATFORM_CONSENT_BUTTON_NOT_READY",
    "LOCATION_CONSENT_BUTTON_NOT_READY",
    "RAW_CDP_PREFLIGHT_ERROR",
    "RAW_CDP_ACTION_ERROR",
}

PREWRITE_PAUSE_FAILURES = {
    "APPROVED_TARGET_PAGE_AMBIGUOUS",
    "CONVERSATION_TARGET_MISMATCH",
    "JOB_TARGET_MISMATCH",
    "ATTACHMENT_TARGET_MISMATCH",
}


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
    if not draft.dispatch_enabled:
        raise ValueError("只读阶段生成的历史草稿不可批准")
    if task.expires_at and task.expires_at < datetime.now(UTC):
        task.status = ActionStatus.EXPIRED.value
        session.commit()
        raise ValueError("确认任务已过期")
    require_transition(task.status, ActionStatus.APPROVED)
    conversation = None
    job_decision = None
    if decision.action_type == ActionType.GREETING.value:
        if draft.job_decision_id is None:
            raise ValueError("招呼语草稿缺少职位沟通决策")
        job_decision, job = _require_greeting_decision(session, draft.job_decision_id)
        if conversation_id is not None:
            raise ValueError("首次招呼尚未建立对话")
    else:
        if conversation_id is None:
            raise ValueError("当前动作必须指定对话")
        conversation, job = _conversation_job(session, conversation_id)
        if draft.conversation_id and draft.conversation_id != conversation.id:
            raise ValueError("草稿与对话不匹配")
    resume = None
    if decision.action_type == ActionType.RESUME.value:
        resume_id = UUID(str(decision.input_snapshot["resume_id"]))
        resume = session.get(db.Resume, resume_id)
        if resume is None or not resume.is_available:
            raise ValueError("简历附件不可用")
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
    platform_default_content = (
        get_conversation_policy().platform_default_greetings.get(job.source)
        if decision.action_type == ActionType.GREETING.value
        else None
    )
    uses_platform_default = (
        decision.action_type == ActionType.GREETING.value and job.source == "BOSS"
    )
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID,
        confirmation_task_id=task.id,
        policy_decision_id=decision.id,
        strategy_id=job_decision.strategy_id if job_decision else None,
        job_id=job.id,
        conversation_id=conversation.id if conversation else None,
        draft_id=draft.id,
        resume_id=resume.id if resume else None,
        action_type=decision.action_type,
        status=ActionStatus.APPROVED.value,
        content=draft.content if decision.action_type != "RESUME" else None,
        delivery_mode=(
            "PLATFORM_DEFAULT" if uses_platform_default else "CUSTOM"
        ),
        expected_platform_content=platform_default_content,
        platform=conversation.platform if conversation else job.source,
        target_company=job.company_name,
        target_job_title=job.title,
        target_recruiter=(
            conversation.recruiter_name
            if conversation
            else _greeting_recruiter(session, decision)
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
        job_decision_id=draft.job_decision_id,
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
        input_snapshot={
            **decision.input_snapshot,
            "source_draft_id": str(draft.id),
        },
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
    try:
        command = _approved_command(session, action)
    except Exception:
        _finish(
            session,
            action,
            attempt,
            ExecutionResult(
                outcome=ExecutionOutcome.FAILED_FINAL,
                error_code="APPROVED_COMMAND_INVALID",
            ),
        )
        return _response(action)
    try:
        result = (executor or PlaywrightActionExecutor(get_browser_selectors())).execute(
            cdp_url, command
        )
    except Exception:
        # 执行器越过调用边界后可能已经写入平台，未知异常不能按发送前失败重试。
        result = ExecutionResult(
            outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
            error_code="EXECUTOR_UNHANDLED_ERROR",
            write_started=True,
        )
    try:
        _finish(session, action, attempt, result)
    except Exception:
        session.rollback()
        _mark_persistence_unknown(session, action.id, attempt.id, result)
    return _response(action)


def reconcile_action(
    session: Session,
    action_id: UUID,
    cdp_url: str,
    observer: PlaywrightActionExecutor | None = None,
) -> ActionResponse:
    action = _get_action(session, action_id)
    if action.status != ActionStatus.OUTCOME_UNKNOWN.value:
        raise ValueError("只有结果不明确的动作可以对账")
    claimed_id = session.scalar(
        update(db.ActionQueue)
        .where(
            db.ActionQueue.id == action.id,
            db.ActionQueue.status == ActionStatus.OUTCOME_UNKNOWN.value,
        )
        .values(
            status=ActionStatus.EXECUTING.value,
            failure_code="RECONCILIATION_IN_PROGRESS",
            started_at=datetime.now(UTC),
            version=db.ActionQueue.version + 1,
        )
        .returning(db.ActionQueue.id)
    )
    if claimed_id is None:
        raise ValueError("动作正在被其他对账任务处理")
    attempt_number = (
        session.scalar(select(func.count()).where(db.ActionAttempt.action_id == action.id)) or 0
    ) + 1
    attempt = db.ActionAttempt(
        action_id=action.id,
        attempt_number=attempt_number,
        status="RECONCILING",
        started_at=datetime.now(UTC),
    )
    session.add(attempt)
    session.commit()
    session.refresh(action)
    try:
        result = (observer or PlaywrightActionExecutor(get_browser_selectors())).observe(
            cdp_url, _approved_command(session, action)
        )
    except Exception:
        result = ExecutionResult(
            outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
            error_code="RECONCILIATION_OBSERVER_ERROR",
        )
    try:
        _finish(session, action, attempt, result, event="ACTION_RECONCILED")
    except Exception:
        session.rollback()
        _mark_persistence_unknown(session, action.id, attempt.id, result)
    return _response(action)


def observe_action(
    session: Session,
    action_id: UUID,
    cdp_url: str,
    observer: PlaywrightActionExecutor | None = None,
) -> ExecutionResult:
    """只读回查任意动作，不改变动作状态。"""
    action = _get_action(session, action_id)
    return (observer or PlaywrightActionExecutor(get_browser_selectors())).observe(
        cdp_url, _approved_command(session, action)
    )


def approve_retry(session: Session, action_id: UUID) -> ActionResponse:
    action = _get_action(session, action_id)
    latest_attempt = session.scalar(
        select(db.ActionAttempt)
        .where(db.ActionAttempt.action_id == action.id)
        .order_by(db.ActionAttempt.attempt_number.desc())
        .limit(1)
    )
    if not (
        action.status == ActionStatus.FAILED_FINAL.value
        and (
            action.failure_code in PREWRITE_RETRYABLE_FAILURES
            or _retry_policy_denied_after_prewrite_failure(action, latest_attempt)
        )
    ):
        require_transition(action.status, ActionStatus.APPROVED)
    before = action.status
    action.status = ActionStatus.APPROVED.value
    action.failure_code = None
    action.version += 1
    _audit(
        session,
        "ACTION_RETRY_APPROVED",
        "action",
        action.id,
        before,
        "APPROVED",
        ["USER_RETRY_AFTER_CONFIRMED_PREWRITE_FAILURE"],
    )
    session.commit()
    session.refresh(action)
    return _response(action)


def _retry_policy_denied_after_prewrite_failure(
    action: db.ActionQueue,
    latest_attempt: db.ActionAttempt | None,
) -> bool:
    return bool(
        action.failure_code == "RETRY_POLICY_DENIED"
        and action.write_started_at is None
        and latest_attempt is not None
        and not latest_attempt.write_started
        and latest_attempt.error_code in PREWRITE_RETRYABLE_FAILURES
    )


def _approved_command(session: Session, action: db.ActionQueue) -> ApprovedCommand:
    job = session.get(db.Job, action.job_id) if action.job_id else None
    return ApprovedCommand(
        action_type=action.action_type,
        platform=action.platform,
        conversation_key=action.target_conversation_key,
        external_job_id=job.external_job_id if job else None,
        source_url=job.source_url if job else None,
        company=action.target_company,
        job_title=action.target_job_title,
        recruiter=action.target_recruiter,
        content=action.content,
        delivery_mode=action.delivery_mode,
        expected_platform_content=action.expected_platform_content,
        attachment_name=action.attachment_name,
        observation_baseline=action.observation_baseline,
    )


def list_tasks(session: Session) -> list[dict[str, object]]:
    now = datetime.now(UTC)
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
        if job_id is None and draft and draft.job_decision_id:
            job_decision = session.get(db.JobDecision, draft.job_decision_id)
            job_id = job_decision.job_id if job_decision else None
        job = session.get(db.Job, job_id) if job_id else None
        result.append(
            {
                "id": task.id,
                "status": (
                    ActionStatus.EXPIRED.value
                    if task.status == ActionStatus.PENDING_APPROVAL.value
                    and task.expires_at
                    and task.expires_at < now
                    else task.status
                ),
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
                "conversation_id": conversation.id if conversation else None,
            }
        )
    return result


def _finish(
    session: Session,
    action: db.ActionQueue,
    attempt: db.ActionAttempt,
    result: ExecutionResult,
    *,
    event: str = "ACTION_EXECUTION_FINISHED",
) -> None:
    target = ActionStatus(result.outcome.value)
    require_transition(action.status, target)
    action.status = target.value
    action.failure_code = result.error_code
    action.finished_at = datetime.now(UTC)
    if result.write_started and action.write_started_at is None:
        action.write_started_at = datetime.now(UTC)
    if result.observation_baseline:
        action.observation_baseline = result.observation_baseline
    action.version += 1
    attempt.status = target.value
    attempt.error_code = result.error_code
    attempt.external_reference = result.external_reference
    attempt.evidence_hash = result.evidence_hash
    attempt.observed_content = result.observed_content
    attempt.write_started = result.write_started
    attempt.observation_baseline = result.observation_baseline
    attempt.finished_at = datetime.now(UTC)
    if result.observed_content:
        action.observed_content = result.observed_content
    if (
        target is ActionStatus.SUCCEEDED
        and action.action_type == ActionType.RESUME.value
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
    if (
        target is ActionStatus.SUCCEEDED
        and action.action_type == ActionType.MISMATCH_DECLINE.value
        and action.conversation_id
    ):
        conversation = session.get(db.Conversation, action.conversation_id)
        if conversation is not None and conversation.state != "DECLINED":
            previous_state = conversation.state
            conversation.state = "DECLINED"
            _audit(
                session,
                "CONVERSATION_DECLINED",
                "conversation",
                conversation.id,
                previous_state,
                "DECLINED",
                ["MISMATCH_DECLINE_SENT"],
            )
    _audit(
        session,
        event,
        "action",
        action.id,
        "EXECUTING",
        target.value,
        [result.error_code] if result.error_code else [],
    )
    session.commit()
    session.refresh(action)


def _mark_persistence_unknown(
    session: Session,
    action_id: UUID,
    attempt_id: UUID,
    result: ExecutionResult,
) -> None:
    """外部调用已经返回但结果落库失败时，以新的短事务保存安全终态。"""
    action = _get_action(session, action_id)
    attempt = session.get(db.ActionAttempt, attempt_id)
    if action.status != ActionStatus.EXECUTING.value:
        return
    action.status = ActionStatus.OUTCOME_UNKNOWN.value
    action.failure_code = "RESULT_PERSISTENCE_FAILED"
    action.finished_at = datetime.now(UTC)
    action.version += 1
    if result.write_started and action.write_started_at is None:
        action.write_started_at = datetime.now(UTC)
    if result.observation_baseline:
        action.observation_baseline = result.observation_baseline
    if attempt is not None:
        attempt.status = ActionStatus.OUTCOME_UNKNOWN.value
        attempt.error_code = "RESULT_PERSISTENCE_FAILED"
        attempt.write_started = result.write_started
        attempt.observation_baseline = result.observation_baseline
        attempt.finished_at = datetime.now(UTC)
    _audit(
        session,
        "ACTION_RESULT_PERSISTENCE_FAILED",
        "action",
        action.id,
        "EXECUTING",
        "OUTCOME_UNKNOWN",
        ["RESULT_PERSISTENCE_FAILED"],
    )
    session.commit()
    session.refresh(action)


def recover_stale_executing_actions(
    session: Session,
    *,
    older_than: datetime,
) -> int:
    """回收失去执行进程的动作；无法证明未写入时统一进入只读对账。"""
    actions = session.scalars(
        select(db.ActionQueue).where(
            db.ActionQueue.status == ActionStatus.EXECUTING.value,
            db.ActionQueue.started_at < older_than,
        )
    ).all()
    for action in actions:
        action.status = ActionStatus.OUTCOME_UNKNOWN.value
        action.failure_code = "STALE_EXECUTION_REQUIRES_RECONCILIATION"
        action.finished_at = datetime.now(UTC)
        action.version += 1
        attempt = session.scalar(
            select(db.ActionAttempt)
            .where(db.ActionAttempt.action_id == action.id)
            .order_by(db.ActionAttempt.attempt_number.desc())
            .limit(1)
        )
        if attempt is not None and attempt.finished_at is None:
            attempt.status = ActionStatus.OUTCOME_UNKNOWN.value
            attempt.error_code = action.failure_code
            attempt.finished_at = datetime.now(UTC)
        _audit(
            session,
            "STALE_ACTION_RECOVERED",
            "action",
            action.id,
            "EXECUTING",
            "OUTCOME_UNKNOWN",
            [action.failure_code],
        )
    if actions:
        session.commit()
    return len(actions)


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


def _greeting_recruiter(session: Session, decision: db.PolicyDecision) -> str:
    recruiter = decision.input_snapshot.get("recruiter_name")
    if recruiter:
        return str(recruiter)
    source_draft_id = decision.input_snapshot.get("source_draft_id")
    if source_draft_id:
        source_decisions = session.scalars(
            select(db.PolicyDecision)
            .where(db.PolicyDecision.draft_id == UUID(str(source_draft_id)))
            .order_by(db.PolicyDecision.created_at.desc())
        ).all()
        for source in source_decisions:
            recruiter = source.input_snapshot.get("recruiter_name")
            if recruiter:
                return str(recruiter)
    raise ValueError("招呼语确认缺少招聘人快照")


def _require_greeting_decision(
    session: Session,
    decision_id: UUID,
) -> tuple[db.JobDecision, db.Job]:
    decision = session.get(db.JobDecision, decision_id)
    if decision is None:
        raise ResourceNotFoundError("职位沟通决策不存在")
    job = session.get(db.Job, decision.job_id)
    strategy = session.get(db.JobStrategy, decision.strategy_id)
    profile = session.get(db.CandidateProfile, decision.candidate_profile_id)
    latest_decision_id = session.scalar(
        select(db.JobDecision.id)
        .where(
            db.JobDecision.job_id == decision.job_id,
            db.JobDecision.strategy_id == decision.strategy_id,
        )
        .order_by(db.JobDecision.created_at.desc())
        .limit(1)
    )
    if (
        job is None
        or strategy is None
        or profile is None
        or latest_decision_id != decision.id
        or strategy.version != decision.strategy_version
        or profile.version != decision.profile_version
        or decision.hard_rejected
        or decision.effective_job_status != "OPEN"
        or decision.decision != "CONTACT"
        or not decision.automation_eligible
    ):
        raise ValueError("职位不满足主动沟通条件或决策已过期")
    return decision, job


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
        delivery_mode=action.delivery_mode,
        expected_platform_content=action.expected_platform_content,
        observed_content=action.observed_content,
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
