import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.automation import AutomationDispatchRequest, AutomationSettingPayload
from apps.api.app.services.action_service import (
    PREWRITE_RETRYABLE_FAILURES,
    approve_retry,
    execute_action,
)
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.browser_worker.actions import ActionExecutor
from packages.policy_engine.automation import (
    AutomationContext,
    AutomationDecision,
    AutomationRules,
    evaluate_automation,
)
from packages.policy_engine.state_machine import ActionStatus, ActionType


def list_automatic_actions(
    session: Session, page: int = 1, page_size: int = 50
) -> tuple[list[dict[str, object]], int]:
    query = (
        select(db.ActionQueue)
        .where(
            db.ActionQueue.user_id == DEFAULT_USER_ID,
            db.ActionQueue.authorization_source == "AUTO",
        )
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(db.ActionQueue.created_at.desc(), db.ActionQueue.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items: list[dict[str, object]] = [
        {
            "id": row.id,
            "agent_run_id": row.agent_run_id,
            "action_type": row.action_type,
            "status": row.status,
            "platform": row.platform,
            "company": row.target_company,
            "job_title": row.target_job_title,
            "recruiter": row.target_recruiter,
            "content": row.content,
            "attachment_name": row.attachment_name,
            "failure_code": row.failure_code,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return items, total

POLICY_VERSION = "automation-policy-v1"


def authorize_automatic_action(
    session: Session,
    *,
    action_type: str,
    platform: str,
    strategy_id: UUID,
    context: AutomationContext,
    draft_id: UUID | None = None,
    safety_blockers: list[str] | None = None,
    input_snapshot: dict[str, object] | None = None,
) -> tuple[AutomationDecision, list[str], db.PolicyDecision]:
    """统一保存自动动作授权；浏览器写动作不得绕过该入口。"""
    if context.action_type != action_type:
        raise ValueError("动作上下文类型不一致")
    rules = effective_rules(session, platform, strategy_id)
    decision, reasons = evaluate_automation(context, rules)
    if safety_blockers:
        decision = AutomationDecision.DENY
        reasons = list(dict.fromkeys(safety_blockers))
    policy = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=draft_id,
        action_type=action_type,
        decision=decision.value,
        reason_codes=reasons,
        policy_version=POLICY_VERSION,
        input_snapshot={
            "rules": rules.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            **(input_snapshot or {}),
        },
    )
    session.add(policy)
    session.flush()
    _audit(session, "AUTOMATION_DECIDED", policy.id, None, decision.value, reasons)
    return decision, reasons, policy


def upsert_setting(session: Session, payload: AutomationSettingPayload) -> dict[str, object]:
    ensure_default_user(session)
    if payload.scope_type == "GLOBAL" and payload.scope_key != "GLOBAL":
        raise ValueError("全局配置的 scope_key 必须为 GLOBAL")
    setting = session.scalar(select(db.AutomationSetting).where(
        db.AutomationSetting.user_id == DEFAULT_USER_ID,
        db.AutomationSetting.scope_type == payload.scope_type,
        db.AutomationSetting.scope_key == payload.scope_key,
    ))
    if setting is None:
        values = payload.model_dump(exclude={"scope_type", "scope_key"})
        setting = db.AutomationSetting(
            user_id=DEFAULT_USER_ID, scope_type=payload.scope_type,
            scope_key=payload.scope_key, **values,
        )
        session.add(setting)
    else:
        supplied_fields = payload.model_fields_set - {"scope_type", "scope_key"}
        values = payload.model_dump(include=supplied_fields)
        for key, value in values.items():
            setattr(setting, key, value)
    session.commit()
    session.refresh(setting)
    return _setting_response(setting)


def list_settings(session: Session) -> list[dict[str, object]]:
    return [_setting_response(item) for item in session.scalars(
        select(db.AutomationSetting).where(db.AutomationSetting.user_id == DEFAULT_USER_ID)
        .order_by(db.AutomationSetting.scope_type, db.AutomationSetting.scope_key)
    ).all()]


def dispatch(
    session: Session,
    payload: AutomationDispatchRequest,
    *,
    executor: ActionExecutor | None = None,
    agent_run_id: UUID | None = None,
) -> dict[str, object]:
    conversation = session.get(db.Conversation, payload.conversation_id)
    draft = session.get(db.GeneratedDraft, payload.draft_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    if draft is None or draft.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("草稿不存在")
    if not draft.dispatch_enabled:
        raise ValueError("只读阶段生成的历史草稿不可派发")
    if draft.conversation_id and draft.conversation_id != conversation.id:
        raise ValueError("草稿与对话不匹配")
    if payload.action_type != draft.draft_type:
        raise ValueError("动作类型与草稿类型不匹配")
    job = (
        session.get(db.Job, conversation.job_id)
        if conversation.job_id
        else None
    )
    inbound_action = payload.action_type != "GREETING"
    if job is None and not inbound_action:
        raise ResourceNotFoundError("职位不存在")
    if inbound_action:
        score = (
            session.get(db.JobScore, conversation.latest_job_score_id)
            if conversation.latest_job_score_id
            else None
        )
    else:
        if job is None:
            raise RuntimeError("评分动作缺少职位")
        score = _score_for_draft(session, draft, job.id)
    strategy_id = (
        score.strategy_id
        if score
        else conversation.strategy_id
    )
    if strategy_id is None:
        raise ValueError("入站动作缺少绑定策略")
    rules = _effective_rules(session, conversation.platform, strategy_id)
    original = session.scalar(
        select(db.PolicyDecision).where(db.PolicyDecision.draft_id == draft.id)
        .order_by(db.PolicyDecision.created_at.asc())
    )
    if original is None:
        raise ValueError("草稿缺少原始策略决策")
    if original.action_type != payload.action_type:
        raise ValueError("动作类型与原始策略决策不匹配")
    if payload.action_type == ActionType.RESUME.value:
        expected_resume_id = original.input_snapshot.get("resume_id")
        if expected_resume_id != str(payload.resume_id):
            raise ValueError("简历附件与草稿策略决策不匹配")
        resume = _resume(session, payload.resume_id, conversation.platform)
    else:
        resume = None
    context = AutomationContext(
        action_type=payload.action_type,
        score=score.total_score if score else 0,
        grade=score.grade if score else "UNKNOWN",
        eligible=(
            conversation.qualification_status != "MISMATCH"
            or payload.action_type == ActionType.MISMATCH_DECLINE.value
        ) if inbound_action else (
            score is not None
            and not score.hard_rejected
            and score.eligibility == "ELIGIBLE"
            and (
                payload.action_type != ActionType.GREETING.value
                or score.automation_eligible
            )
        ),
        job_open=(score.effective_job_status == "OPEN") if score else True,
        confidence=float(draft.confidence),
        original_decision=original.decision,
        intents=draft.intents,
        has_verified_facts=bool(draft.fact_ids),
        explicit_resume_request="RESUME_REQUEST" in draft.intents,
        resume_available=resume is not None,
        resume_already_sent=bool(resume and session.scalar(select(db.ResumeSendRecord).where(
            db.ResumeSendRecord.conversation_id == conversation.id,
            db.ResumeSendRecord.resume_id == resume.id,
        ))),
        qualification_status=conversation.qualification_status,
    )
    decision, reasons = evaluate_automation(context, rules)
    if inbound_action and not conversation.identity_reliable:
        decision = AutomationDecision.DENY
        reasons = ["CONVERSATION_IDENTITY_UNRELIABLE"]
    if inbound_action and conversation.platform == "LIEPIN":
        identity_gaps = _liepin_inbound_identity_gaps(conversation, job)
        if identity_gaps:
            decision = AutomationDecision.DENY
            reasons = identity_gaps
    policy = db.PolicyDecision(
        user_id=DEFAULT_USER_ID, draft_id=draft.id, action_type=payload.action_type,
        decision=decision.value, reason_codes=reasons, policy_version=POLICY_VERSION,
        input_snapshot={"rules": rules.model_dump(mode="json"), "context": context.model_dump(mode="json")},
    )
    session.add(policy)
    session.flush()
    if decision is not AutomationDecision.ALLOW_AUTO:
        _audit(session, "AUTOMATION_DECIDED", policy.id, None, decision.value, reasons)
        session.commit()
        return {"decision": decision.value, "reason_codes": reasons}
    action = _create_auto_action(
        session,
        conversation,
        job,
        strategy_id,
        draft,
        original,
        policy,
        resume,
        agent_run_id,
    )
    pending_task = session.scalar(select(db.ConfirmationTask).where(
        db.ConfirmationTask.decision_id == original.id,
        db.ConfirmationTask.status == "PENDING_APPROVAL",
    ))
    if pending_task:
        pending_task.status = "SUPERSEDED"
    session.commit()
    if (
        action.status == ActionStatus.FAILED_RETRYABLE.value
        and action.failure_code in PREWRITE_RETRYABLE_FAILURES
    ):
        approve_retry(session, action.id)
    result = execute_action(session, action.id, payload.cdp_url, executor)
    if (
        result.status == ActionStatus.SUCCEEDED.value
        and payload.action_type == ActionType.MISMATCH_DECLINE.value
    ):
        conversation.state = "DECLINED"
        _audit(
            session,
            "CONVERSATION_DECLINED",
            conversation.id,
            "ACTIVE",
            "DECLINED",
            ["MISMATCH_DECLINE_SENT"],
        )
        session.commit()
    if result.status in {"FAILED_FINAL", "OUTCOME_UNKNOWN"}:
        _pause_platform(session, conversation.platform, result.failure_code or result.status)
    return {"decision": decision.value, "reason_codes": reasons,
            "action_id": result.id, "action_status": result.status,
            "failure_code": result.failure_code}


def dispatch_proactive_greeting(
    session: Session,
    job_id: UUID,
    draft_id: UUID,
    recruiter_name: str,
    cdp_url: str,
    *,
    executor: ActionExecutor,
    agent_run_id: UUID,
    platform: str = "BOSS",
) -> dict[str, object]:
    job = session.get(db.Job, job_id)
    draft = session.get(db.GeneratedDraft, draft_id)
    if job is None or job.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("职位不存在")
    if draft is None or draft.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("招呼草稿不存在")
    score = _score_for_draft(session, draft, job.id)
    rules = _effective_rules(session, platform, score.strategy_id)
    original = session.scalar(
        select(db.PolicyDecision)
        .where(db.PolicyDecision.draft_id == draft.id)
        .order_by(db.PolicyDecision.created_at.asc())
    )
    if original is None:
        raise ValueError("招呼草稿缺少原始策略决策")
    context = AutomationContext(
        action_type=ActionType.GREETING.value,
        score=score.total_score,
        grade=score.grade,
        eligible=(
            score.automation_eligible
            and not score.hard_rejected
            and score.eligibility == "ELIGIBLE"
        ),
        job_open=score.effective_job_status == "OPEN",
        confidence=float(draft.confidence),
        original_decision=original.decision,
        has_verified_facts=bool(draft.fact_ids),
    )
    decision, reasons = evaluate_automation(context, rules)
    missing = _proactive_safety_gaps(job, recruiter_name)
    if missing:
        decision = AutomationDecision.DENY
        reasons = missing
    policy = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=draft.id,
        action_type=ActionType.GREETING.value,
        decision=decision.value,
        reason_codes=reasons,
        policy_version=POLICY_VERSION,
        input_snapshot={
            "rules": rules.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        },
    )
    session.add(policy)
    session.flush()
    if decision is not AutomationDecision.ALLOW_AUTO:
        _audit(session, "AUTOMATION_DECIDED", policy.id, None, decision.value, reasons)
        session.commit()
        return {"decision": decision.value, "reason_codes": reasons}
    fingerprint = hashlib.sha256(
        f"{platform}:{ActionType.GREETING.value}:{job.external_job_id or job.id}".encode()
    ).hexdigest()
    existing = session.scalar(
        select(db.ActionQueue).where(db.ActionQueue.send_fingerprint == fingerprint)
    )
    if existing:
        if (
            existing.status == ActionStatus.FAILED_RETRYABLE.value
            and existing.failure_code in PREWRITE_RETRYABLE_FAILURES
        ):
            # 重试前使用本轮重新读取并核验过的职位事实，避免沿用旧页面中的状态文本。
            existing.target_company = job.company_name
            existing.target_job_title = job.title
            existing.target_recruiter = recruiter_name
            approve_retry(session, existing.id)
            result = execute_action(session, existing.id, cdp_url, executor)
            return {
                "decision": decision.value,
                "reason_codes": ["GREETING_PREWRITE_RETRY"],
                "action_id": result.id,
                "action_status": result.status,
                "failure_code": result.failure_code,
            }
        return {
            "decision": "DENY",
            "reason_codes": ["GREETING_ALREADY_EXISTS"],
            "action_id": existing.id,
            "action_status": existing.status,
        }
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID,
        policy_decision_id=policy.id,
        strategy_id=score.strategy_id,
        authorization_source="AUTO",
        agent_run_id=agent_run_id,
        job_id=job.id,
        draft_id=draft.id,
        action_type=ActionType.GREETING.value,
        status=ActionStatus.APPROVED.value,
        content=draft.content,
        delivery_mode=(
            "PLATFORM_DEFAULT"
            if platform in {"BOSS", "LIEPIN"}
            else "CUSTOM"
        ),
        platform=platform,
        target_company=job.company_name,
        target_job_title=job.title,
        target_recruiter=recruiter_name,
        idempotency_key=f"auto:{fingerprint}",
        send_fingerprint=fingerprint,
        approved_at=datetime.now(UTC),
    )
    session.add(action)
    session.flush()
    _audit(session, "AUTO_ACTION_ALLOWED", action.id, None, "APPROVED", reasons)
    session.commit()
    result = execute_action(session, action.id, cdp_url, executor)
    if result.status in {"FAILED_FINAL", "OUTCOME_UNKNOWN"}:
        _pause_platform(session, platform, result.failure_code or result.status)
    return {
        "decision": decision.value,
        "reason_codes": reasons,
        "action_id": result.id,
        "action_status": result.status,
        "failure_code": result.failure_code,
    }


def _proactive_safety_gaps(job: db.Job, recruiter_name: str) -> list[str]:
    reasons: list[str] = []
    if not job.company_name or job.company_name in {"匿名公司", "某公司", "保密"}:
        reasons.append("ANONYMOUS_COMPANY")
    if not recruiter_name:
        reasons.append("RECRUITER_UNKNOWN")
    if not job.external_job_id:
        reasons.append("EXTERNAL_JOB_ID_MISSING")
    return reasons


def _liepin_inbound_identity_gaps(
    conversation: db.Conversation,
    job: db.Job | None,
) -> list[str]:
    reasons: list[str] = []
    if job is None or not job.external_job_id:
        reasons.append("LIEPIN_LINKED_JOB_ID_MISSING")
    if job is None or not job.company_name or not job.title:
        reasons.append("LIEPIN_LINKED_JOB_IDENTITY_INCOMPLETE")
    if not conversation.recruiter_name or not conversation.external_conversation_id:
        reasons.append("LIEPIN_CONVERSATION_IDENTITY_INCOMPLETE")
    return reasons


def effective_rules(session: Session, platform: str, strategy_id: UUID) -> AutomationRules:
    rows = session.scalars(select(db.AutomationSetting).where(
        db.AutomationSetting.user_id == DEFAULT_USER_ID,
        ((db.AutomationSetting.scope_type == "GLOBAL") & (db.AutomationSetting.scope_key == "GLOBAL")) |
        ((db.AutomationSetting.scope_type == "PLATFORM") & (db.AutomationSetting.scope_key == platform)) |
        ((db.AutomationSetting.scope_type == "STRATEGY") & (db.AutomationSetting.scope_key == str(strategy_id))),
    )).all()
    global_row = next((row for row in rows if row.scope_type == "GLOBAL"), None)
    if global_row is None:
        return AutomationRules()
    merged = _rules(global_row).model_dump()
    for row in rows:
        if row is global_row:
            continue
        merged["enabled"] = bool(merged["enabled"]) and row.enabled
        merged["paused"] = bool(merged["paused"]) or row.paused
        merged["auto_greet_enabled"] = bool(merged["auto_greet_enabled"]) and row.auto_greet_enabled
        merged["auto_reply_enabled"] = bool(merged["auto_reply_enabled"]) and row.auto_reply_enabled
        merged["auto_resume_enabled"] = bool(merged["auto_resume_enabled"]) and row.auto_resume_enabled
        merged["maimai_recommendation_enabled"] = (
            bool(merged["maimai_recommendation_enabled"])
            and row.maimai_recommendation_enabled
        )
        merged["maimai_recommendation_resume_enabled"] = (
            bool(merged["maimai_recommendation_resume_enabled"])
            and row.maimai_recommendation_resume_enabled
        )
        merged["auto_greet_min_score"] = max(int(merged["auto_greet_min_score"]), row.auto_greet_min_score)
        merged["emergency_stop"] = bool(merged["emergency_stop"]) or row.emergency_stop
        merged["job_scan_enabled"] = bool(merged["job_scan_enabled"]) and row.job_scan_enabled
        merged["company_cooldown_hours"] = max(
            int(merged["company_cooldown_hours"]), row.company_cooldown_hours
        )
        merged["recruiter_cooldown_hours"] = max(
            int(merged["recruiter_cooldown_hours"]), row.recruiter_cooldown_hours
        )
        merged["work_start_hour"] = max(int(merged["work_start_hour"]), row.work_start_hour)
        merged["work_end_hour"] = min(int(merged["work_end_hour"]), row.work_end_hour)
    return AutomationRules.model_validate(merged)


_effective_rules = effective_rules


def _rules(row: db.AutomationSetting) -> AutomationRules:
    return AutomationRules(
        enabled=row.enabled, paused=row.paused,
        auto_greet_enabled=row.auto_greet_enabled, auto_greet_min_score=row.auto_greet_min_score,
        auto_reply_enabled=row.auto_reply_enabled,
        auto_resume_enabled=row.auto_resume_enabled,
        maimai_recommendation_enabled=row.maimai_recommendation_enabled,
        maimai_recommendation_resume_enabled=row.maimai_recommendation_resume_enabled,
        emergency_stop=row.emergency_stop,
        job_scan_enabled=row.job_scan_enabled,
        company_cooldown_hours=row.company_cooldown_hours,
        recruiter_cooldown_hours=row.recruiter_cooldown_hours,
        work_start_hour=row.work_start_hour,
        work_end_hour=row.work_end_hour,
    )


def _score_for_draft(session: Session, draft: db.GeneratedDraft, job_id: UUID) -> db.JobScore:
    score = session.get(db.JobScore, draft.job_score_id) if draft.job_score_id else None
    if score is None:
        score = session.scalar(select(db.JobScore).where(db.JobScore.job_id == job_id)
                               .order_by(db.JobScore.created_at.desc()))
    if score is None:
        raise ValueError("职位缺少评分结果")
    strategy = session.get(db.JobStrategy, score.strategy_id)
    if strategy is None or score.strategy_version != strategy.version:
        raise ValueError("评分使用的策略版本已过期，必须重新评分")
    profile = session.get(db.CandidateProfile, score.candidate_profile_id)
    if profile is None or score.profile_version != profile.version:
        raise ValueError("评分使用的候选人资料版本已过期，必须重新评分")
    return score


def _resume(session: Session, resume_id: UUID | None, platform: str) -> db.Resume:
    if resume_id is None:
        raise ValueError("自动发送简历必须指定网站内附件")
    resume = session.get(db.Resume, resume_id)
    if resume is None or resume.user_id != DEFAULT_USER_ID or not resume.is_available or resume.platform != platform:
        raise ValueError("简历附件不存在、不可用或平台不匹配")
    return resume


def _create_auto_action(session: Session, conversation: db.Conversation, job: db.Job | None,
                        strategy_id: UUID,
                        draft: db.GeneratedDraft,
                        original: db.PolicyDecision,
                        policy: db.PolicyDecision, resume: db.Resume | None,
                        agent_run_id: UUID | None = None) -> db.ActionQueue:
    fingerprint = hashlib.sha256(
        f"{conversation.id}:{policy.action_type}:{draft.content}:{resume.id if resume else ''}".encode()
    ).hexdigest()
    existing = session.scalar(select(db.ActionQueue).where(db.ActionQueue.send_fingerprint == fingerprint))
    if existing:
        return existing
    evidence = original.input_snapshot.get("evidence_message_ids")
    evidence_message_ids = (
        [str(item) for item in evidence]
        if isinstance(evidence, list)
        else conversation.qualification_message_ids
    )
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID, confirmation_task_id=None, policy_decision_id=policy.id,
        strategy_id=strategy_id, authorization_source="AUTO",
        authorization_basis=str(
            original.input_snapshot.get("authorization_basis")
            or (
                "QUALIFICATION_MISMATCH"
                if policy.action_type == ActionType.MISMATCH_DECLINE.value
                else "AUTOMATION_POLICY"
            )
        ),
        qualification_snapshot={
            "status": conversation.qualification_status,
            "evidence": conversation.qualification_evidence,
            "version": conversation.qualification_version,
        },
        evidence_message_ids=evidence_message_ids,
        agent_run_id=agent_run_id,
        conversation_id=conversation.id, draft_id=draft.id, resume_id=resume.id if resume else None,
        action_type=policy.action_type, status=ActionStatus.APPROVED.value,
        content=None if resume else draft.content, platform=conversation.platform,
        target_company=(
            job.company_name
            if job
            else conversation.observed_company_name or "未知公司"
        ),
        target_job_title=(
            job.title if job else conversation.observed_job_title or "未知岗位"
        ),
        target_recruiter=conversation.recruiter_name,
        target_conversation_key=conversation.external_conversation_id,
        attachment_name=resume.attachment_name if resume else None,
        idempotency_key=f"auto:{fingerprint}", send_fingerprint=fingerprint,
        approved_at=datetime.now(UTC),
    )
    session.add(action)
    session.flush()
    _audit(session, "AUTO_ACTION_ALLOWED", action.id, None, "APPROVED", policy.reason_codes)
    return action


def _pause_platform(session: Session, platform: str, reason: str) -> None:
    setting = session.scalar(select(db.AutomationSetting).where(
        db.AutomationSetting.user_id == DEFAULT_USER_ID,
        db.AutomationSetting.scope_type == "PLATFORM",
        db.AutomationSetting.scope_key == platform,
    ))
    if setting is None:
        global_setting = session.scalar(select(db.AutomationSetting).where(
            db.AutomationSetting.user_id == DEFAULT_USER_ID,
            db.AutomationSetting.scope_type == "GLOBAL",
            db.AutomationSetting.scope_key == "GLOBAL",
        ))
        values = (
            _rules(global_setting).model_dump()
            if global_setting is not None
            else AutomationRules().model_dump()
        )
        values["paused"] = True
        setting = db.AutomationSetting(
            user_id=DEFAULT_USER_ID,
            scope_type="PLATFORM",
            scope_key=platform,
            **values,
        )
        session.add(setting)
    else:
        setting.paused = True
    session.flush()
    _audit(session, "PLATFORM_AUTOMATION_PAUSED", setting.id, None, "PAUSED", [reason])
    session.commit()


def _audit(session: Session, event: str, entity_id: UUID, before: str | None,
           after: str, reasons: list[str]) -> None:
    session.add(db.AuditEvent(
        user_id=DEFAULT_USER_ID, actor_type="SYSTEM", event_type=event,
        entity_type="automation", entity_id=entity_id, before_state=before,
        after_state=after, reason_codes=reasons, metadata_json={},
        correlation_id=f"automation:{entity_id}",
    ))


def _setting_response(item: db.AutomationSetting) -> dict[str, object]:
    return {"id": item.id, "scope_type": item.scope_type, "scope_key": item.scope_key,
            **_rules(item).model_dump()}
