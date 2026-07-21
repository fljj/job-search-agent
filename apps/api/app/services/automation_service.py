import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.automation import AutomationDispatchRequest, AutomationSettingPayload
from apps.api.app.services.action_service import execute_action
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.policy_engine.automation import (
    AutomationContext,
    AutomationDecision,
    AutomationRules,
    evaluate_automation,
)
from packages.policy_engine.state_machine import ActionStatus

POLICY_VERSION = "automation-policy-v1"


def upsert_setting(session: Session, payload: AutomationSettingPayload) -> dict[str, object]:
    ensure_default_user(session)
    if payload.scope_type == "GLOBAL" and payload.scope_key != "GLOBAL":
        raise ValueError("全局配置的 scope_key 必须为 GLOBAL")
    setting = session.scalar(select(db.AutomationSetting).where(
        db.AutomationSetting.user_id == DEFAULT_USER_ID,
        db.AutomationSetting.scope_type == payload.scope_type,
        db.AutomationSetting.scope_key == payload.scope_key,
    ))
    values = payload.model_dump(exclude={"scope_type", "scope_key"})
    if setting is None:
        setting = db.AutomationSetting(
            user_id=DEFAULT_USER_ID, scope_type=payload.scope_type,
            scope_key=payload.scope_key, **values,
        )
        session.add(setting)
    else:
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


def dispatch(session: Session, payload: AutomationDispatchRequest) -> dict[str, object]:
    conversation = session.get(db.Conversation, payload.conversation_id)
    draft = session.get(db.GeneratedDraft, payload.draft_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    if draft is None or draft.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("草稿不存在")
    if draft.conversation_id and draft.conversation_id != conversation.id:
        raise ValueError("草稿与对话不匹配")
    job = session.get(db.Job, conversation.job_id)
    if job is None:
        raise ResourceNotFoundError("职位不存在")
    score = _score_for_draft(session, draft, job.id)
    rules = _effective_rules(session, conversation.platform, score.strategy_id)
    original = session.scalar(
        select(db.PolicyDecision).where(db.PolicyDecision.draft_id == draft.id)
        .order_by(db.PolicyDecision.created_at.asc())
    )
    if original is None:
        raise ValueError("草稿缺少原始策略决策")
    resume = _resume(session, payload.resume_id, conversation.platform) if payload.action_type == "RESUME" else None
    counts = _rate_counts(session, conversation.platform)
    context = AutomationContext(
        action_type=payload.action_type,
        score=score.total_score,
        grade=score.grade,
        eligible=not score.hard_rejected and score.eligibility == "ELIGIBLE",
        job_open=score.effective_job_status == "OPEN",
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
        hourly_count=counts[0], daily_count=counts[1],
    )
    decision, reasons = evaluate_automation(context, rules)
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
    action = _create_auto_action(session, conversation, job, score, draft, policy, resume)
    pending_task = session.scalar(select(db.ConfirmationTask).where(
        db.ConfirmationTask.decision_id == original.id,
        db.ConfirmationTask.status == "PENDING_APPROVAL",
    ))
    if pending_task:
        pending_task.status = "SUPERSEDED"
    session.commit()
    result = execute_action(session, action.id, payload.cdp_url)
    if result.status in {"FAILED_FINAL", "OUTCOME_UNKNOWN"}:
        _pause_platform(session, conversation.platform, result.failure_code or result.status)
    return {"decision": decision.value, "reason_codes": reasons,
            "action_id": result.id, "action_status": result.status}


def _effective_rules(session: Session, platform: str, strategy_id: UUID) -> AutomationRules:
    rows = session.scalars(select(db.AutomationSetting).where(
        db.AutomationSetting.user_id == DEFAULT_USER_ID,
        ((db.AutomationSetting.scope_type == "GLOBAL") & (db.AutomationSetting.scope_key == "GLOBAL")) |
        ((db.AutomationSetting.scope_type == "PLATFORM") & (db.AutomationSetting.scope_key == platform)) |
        ((db.AutomationSetting.scope_type == "STRATEGY") & (db.AutomationSetting.scope_key == str(strategy_id))),
    )).all()
    global_row = next((row for row in rows if row.scope_type == "GLOBAL"), None)
    if global_row is None:
        return AutomationRules()
    rules = _rules(global_row)
    for row in rows:
        if row is global_row:
            continue
        rules.enabled = rules.enabled and row.enabled
        rules.paused = rules.paused or row.paused
        rules.auto_greet_enabled = rules.auto_greet_enabled and row.auto_greet_enabled
        rules.auto_reply_enabled = rules.auto_reply_enabled and row.auto_reply_enabled
        rules.auto_resume_enabled = rules.auto_resume_enabled and row.auto_resume_enabled
        rules.auto_greet_min_score = max(rules.auto_greet_min_score, row.auto_greet_min_score)
        rules.auto_reply_min_confidence = max(rules.auto_reply_min_confidence, float(row.auto_reply_min_confidence))
        rules.auto_resume_min_score = max(rules.auto_resume_min_score, row.auto_resume_min_score)
        rules.hourly_limit = min(rules.hourly_limit, row.hourly_limit)
        rules.daily_limit = min(rules.daily_limit, row.daily_limit)
    return rules


def _rules(row: db.AutomationSetting) -> AutomationRules:
    return AutomationRules(
        enabled=row.enabled, paused=row.paused,
        auto_greet_enabled=row.auto_greet_enabled, auto_greet_min_score=row.auto_greet_min_score,
        auto_reply_enabled=row.auto_reply_enabled,
        auto_reply_min_confidence=float(row.auto_reply_min_confidence),
        auto_resume_enabled=row.auto_resume_enabled, auto_resume_min_score=row.auto_resume_min_score,
        hourly_limit=row.hourly_limit, daily_limit=row.daily_limit,
    )


def _score_for_draft(session: Session, draft: db.GeneratedDraft, job_id: UUID) -> db.JobScore:
    score = session.get(db.JobScore, draft.job_score_id) if draft.job_score_id else None
    if score is None:
        score = session.scalar(select(db.JobScore).where(db.JobScore.job_id == job_id)
                               .order_by(db.JobScore.created_at.desc()))
    if score is None:
        raise ValueError("职位缺少评分结果")
    return score


def _resume(session: Session, resume_id: UUID | None, platform: str) -> db.Resume:
    if resume_id is None:
        raise ValueError("自动发送简历必须指定网站内附件")
    resume = session.get(db.Resume, resume_id)
    if resume is None or resume.user_id != DEFAULT_USER_ID or not resume.is_available or resume.platform != platform:
        raise ValueError("简历附件不存在、不可用或平台不匹配")
    return resume


def _rate_counts(session: Session, platform: str) -> tuple[int, int]:
    now = datetime.now(UTC)
    def count(since: datetime) -> int:
        return session.scalar(select(func.count()).select_from(db.ActionQueue).where(
            db.ActionQueue.user_id == DEFAULT_USER_ID,
            db.ActionQueue.platform == platform,
            db.ActionQueue.authorization_source == "AUTO",
            db.ActionQueue.created_at >= since,
        )) or 0
    return count(now - timedelta(hours=1)), count(now - timedelta(days=1))


def _create_auto_action(session: Session, conversation: db.Conversation, job: db.Job,
                        score: db.JobScore, draft: db.GeneratedDraft,
                        policy: db.PolicyDecision, resume: db.Resume | None) -> db.ActionQueue:
    fingerprint = hashlib.sha256(
        f"{conversation.id}:{policy.action_type}:{draft.content}:{resume.id if resume else ''}".encode()
    ).hexdigest()
    existing = session.scalar(select(db.ActionQueue).where(db.ActionQueue.send_fingerprint == fingerprint))
    if existing:
        return existing
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID, confirmation_task_id=None, policy_decision_id=policy.id,
        strategy_id=score.strategy_id, authorization_source="AUTO",
        conversation_id=conversation.id, draft_id=draft.id, resume_id=resume.id if resume else None,
        action_type=policy.action_type, status=ActionStatus.APPROVED.value,
        content=None if resume else draft.content, platform=conversation.platform,
        target_company=job.company_name, target_job_title=job.title,
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
        setting = db.AutomationSetting(user_id=DEFAULT_USER_ID, scope_type="PLATFORM",
                                       scope_key=platform, paused=True)
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
