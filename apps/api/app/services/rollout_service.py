from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.rollout import RolloutCreateRequest, RolloutTransitionRequest
from apps.api.app.services.errors import ResourceNotFoundError, VersionConflictError
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.policy_engine.rollout import (
    LEVEL_NAMES,
    RolloutLevel,
    action_limit,
    allows_job_scan,
)

SAFETY_FAILURE_CODES = {
    "CONVERSATION_TARGET_MISMATCH",
    "JOB_TARGET_MISMATCH",
    "PAGE_STRUCTURE_CHANGED",
}


def get_or_create_rollout(
    session: Session, payload: RolloutCreateRequest
) -> dict[str, object]:
    ensure_default_user(session)
    row = _find(session, payload.platform)
    if row is None:
        row = db.RolloutControl(
            user_id=DEFAULT_USER_ID,
            platform=payload.platform,
            status="PAUSED",
            current_level=RolloutLevel.MESSAGE_READ_ONLY,
            previous_level=RolloutLevel.MESSAGE_READ_ONLY,
            stage_started_at=datetime.now(UTC),
            minimum_stage_hours=payload.minimum_stage_hours,
            reply_daily_limit=payload.reply_daily_limit,
            greeting_daily_limit=payload.greeting_daily_limit,
        )
        session.add(row)
    else:
        row.minimum_stage_hours = payload.minimum_stage_hours
        row.reply_daily_limit = payload.reply_daily_limit
        row.greeting_daily_limit = payload.greeting_daily_limit
        row.version += 1
    session.commit()
    session.refresh(row)
    return rollout_status(session, row.platform)


def list_rollouts(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(
        select(db.RolloutControl)
        .where(db.RolloutControl.user_id == DEFAULT_USER_ID)
        .order_by(db.RolloutControl.platform)
    ).all()
    return [_response(session, row) for row in rows]


def transition_rollout(
    session: Session, platform: str, payload: RolloutTransitionRequest
) -> dict[str, object]:
    row = _required(session, platform)
    if row.version != payload.expected_version:
        raise VersionConflictError("灰度配置已更新，请刷新后重试")
    before = f"{row.status}:{row.current_level}"
    now = datetime.now(UTC)
    reasons: list[str] = []
    if payload.action == "ACTIVATE":
        row.status = "ACTIVE"
        row.stage_started_at = now
    elif payload.action == "PAUSE":
        row.status = "PAUSED"
    elif payload.action == "ROLLBACK":
        _rollback(row, now)
    else:
        if row.status != "ACTIVE":
            raise ValueError("只有运行中的灰度可以升级")
        if row.current_level >= RolloutLevel.FORMAL_LIMITS:
            raise ValueError("已处于最高灰度级别")
        remaining = _remaining_hours(row, now)
        if remaining > 0:
            raise ValueError(f"当前级别尚需运行 {remaining} 小时")
        metrics = calculate_safety_metrics(session, row)
        reasons = [code for code, count in metrics.items() if count > 0]
        if reasons:
            raise ValueError(f"安全指标未通过：{','.join(reasons)}")
        row.previous_level = row.current_level
        row.current_level += 1
        row.stage_started_at = now
    row.version += 1
    _audit(session, row, "ROLLOUT_TRANSITION", before, reasons or [payload.action])
    session.commit()
    return _response(session, row)


def rollout_status(session: Session, platform: str) -> dict[str, object]:
    return _response(session, _required(session, platform))


def allows_rollout_job_scan(session: Session, platform: str) -> bool:
    if platform == "MOCK":
        return True
    row = _find(session, platform)
    return bool(row and row.status == "ACTIVE" and allows_job_scan(row.current_level))


def evaluate_rollout_action(
    session: Session,
    platform: str,
    action_type: str,
    formal_daily_limit: int,
) -> tuple[bool, list[str]]:
    if platform == "MOCK":
        return True, []
    row = _find(session, platform)
    if row is None:
        return False, ["ROLLOUT_NOT_CONFIGURED"]
    if row.status != "ACTIVE":
        return False, ["ROLLOUT_PAUSED"]
    limit = action_limit(
        row.current_level,
        action_type,
        reply_daily_limit=row.reply_daily_limit,
        greeting_daily_limit=row.greeting_daily_limit,
        formal_daily_limit=formal_daily_limit,
    )
    if limit == 0:
        return False, ["ROLLOUT_ACTION_NOT_ENABLED"]
    since = datetime.now(UTC) - timedelta(days=1)
    used = session.scalar(
        select(func.count())
        .select_from(db.ActionQueue)
        .where(
            db.ActionQueue.user_id == DEFAULT_USER_ID,
            db.ActionQueue.platform == platform,
            db.ActionQueue.authorization_source == "AUTO",
            db.ActionQueue.action_type == action_type,
            db.ActionQueue.created_at >= since,
        )
    ) or 0
    if used >= limit:
        return False, ["ROLLOUT_DAILY_LIMIT_REACHED"]
    return True, []


def enforce_rollout_health(session: Session, platform: str) -> dict[str, object] | None:
    row = _find(session, platform)
    if row is None or row.status != "ACTIVE":
        return None
    metrics = calculate_safety_metrics(session, row)
    reasons = [code for code, count in metrics.items() if count > 0]
    if not reasons:
        return _response(session, row)
    before = f"{row.status}:{row.current_level}"
    _rollback(row, datetime.now(UTC))
    row.version += 1
    _audit(session, row, "ROLLOUT_AUTO_ROLLBACK", before, reasons)
    session.commit()
    return _response(session, row)


def calculate_safety_metrics(
    session: Session, row: db.RolloutControl
) -> dict[str, int]:
    since = row.stage_started_at
    base = (
        db.ActionQueue.user_id == DEFAULT_USER_ID,
        db.ActionQueue.platform == row.platform,
        db.ActionQueue.authorization_source == "AUTO",
        db.ActionQueue.created_at >= since,
    )
    actions = session.scalars(
        select(db.ActionQueue).where(*base).order_by(db.ActionQueue.created_at)
    ).all()
    unsafe_identity = 0
    success_without_evidence = 0
    unknown_without_reconciliation = 0
    duplicate_send = 0
    unscored_write = 0
    headhunter_greeting = 0
    time_commitment = 0
    blind_retry = 0
    successful_fingerprints: set[tuple[object, ...]] = set()
    for action in actions:
        attempts = session.scalars(
            select(db.ActionAttempt)
            .where(db.ActionAttempt.action_id == action.id)
            .order_by(db.ActionAttempt.attempt_number)
        ).all()
        if action.failure_code in SAFETY_FAILURE_CODES:
            unsafe_identity += 1
        if action.status == "SUCCEEDED" and not any(
            attempt.status == "SUCCEEDED" and attempt.evidence_hash
            for attempt in attempts
        ):
            success_without_evidence += 1
        if action.status == "OUTCOME_UNKNOWN":
            reconciliation = session.scalar(
                select(db.ReconciliationTask.id).where(
                    db.ReconciliationTask.action_id == action.id
                )
            )
            if reconciliation is None:
                unknown_without_reconciliation += 1
            if len(attempts) > 1:
                blind_retry += 1
        if action.status == "SUCCEEDED":
            fingerprint = (
                action.conversation_id,
                action.action_type,
                action.content,
                action.resume_id,
            )
            if fingerprint in successful_fingerprints:
                duplicate_send += 1
            successful_fingerprints.add(fingerprint)
        draft = session.get(db.GeneratedDraft, action.draft_id) if action.draft_id else None
        if draft is None or draft.job_score_id is None:
            unscored_write += 1
        if draft and any(
            intent in draft.intents
            for intent in ("PHONE_CALL", "INTERVIEW_INVITATION", "INTERVIEW_TIME")
        ):
            time_commitment += 1
        if action.action_type == "GREETING" and action.job_id:
            score = session.scalar(
                select(db.JobScore)
                .where(db.JobScore.job_id == action.job_id)
                .order_by(db.JobScore.created_at.desc())
            )
            if score and any("HEADHUNTER" in blocker for blocker in score.action_blockers):
                headhunter_greeting += 1
    return {
        "CROSS_CONVERSATION": unsafe_identity,
        "DUPLICATE_MESSAGE_OR_RESUME": duplicate_send,
        "UNSCORED_WRITE": unscored_write,
        "HEADHUNTER_GREETING": headhunter_greeting,
        "TIME_COMMITMENT_WITHOUT_CONFIRMATION": time_commitment,
        "WRITE_AFTER_PAGE_IDENTITY_MISMATCH": unsafe_identity,
        "SUCCESS_WITHOUT_READBACK_EVIDENCE": success_without_evidence,
        "UNKNOWN_WITHOUT_RECONCILIATION": unknown_without_reconciliation,
        "BLIND_RETRY_AFTER_UNKNOWN": blind_retry,
    }


def _rollback(row: db.RolloutControl, now: datetime) -> None:
    if row.current_level <= RolloutLevel.MESSAGE_READ_ONLY:
        row.status = "PAUSED"
        row.previous_level = RolloutLevel.MESSAGE_READ_ONLY
    else:
        row.previous_level = row.current_level
        row.current_level -= 1
    row.stage_started_at = now


def _remaining_hours(row: db.RolloutControl, now: datetime) -> int:
    ready_at = row.stage_started_at + timedelta(hours=row.minimum_stage_hours)
    if ready_at <= now:
        return 0
    seconds = (ready_at - now).total_seconds()
    return max(1, int((seconds + 3599) // 3600))


def _find(session: Session, platform: str) -> db.RolloutControl | None:
    return session.scalar(
        select(db.RolloutControl).where(
            db.RolloutControl.user_id == DEFAULT_USER_ID,
            db.RolloutControl.platform == platform,
        )
    )


def _required(session: Session, platform: str) -> db.RolloutControl:
    row = _find(session, platform)
    if row is None:
        raise ResourceNotFoundError("灰度配置不存在")
    return row


def _response(session: Session, row: db.RolloutControl) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": row.id,
        "platform": row.platform,
        "status": row.status,
        "current_level": row.current_level,
        "level_name": LEVEL_NAMES[RolloutLevel(row.current_level)],
        "previous_level": row.previous_level,
        "stage_started_at": row.stage_started_at.isoformat(),
        "minimum_stage_hours": row.minimum_stage_hours,
        "remaining_hours": _remaining_hours(row, now),
        "reply_daily_limit": row.reply_daily_limit,
        "greeting_daily_limit": row.greeting_daily_limit,
        "safety_metrics": calculate_safety_metrics(session, row),
        "version": row.version,
    }


def _audit(
    session: Session,
    row: db.RolloutControl,
    event_type: str,
    before: str,
    reasons: list[str],
) -> None:
    session.add(
        db.AuditEvent(
            user_id=DEFAULT_USER_ID,
            actor_type="USER" if event_type == "ROLLOUT_TRANSITION" else "SYSTEM",
            event_type=event_type,
            entity_type="rollout",
            entity_id=row.id,
            before_state=before,
            after_state=f"{row.status}:{row.current_level}",
            reason_codes=reasons,
            metadata_json={"platform": row.platform},
            correlation_id=f"rollout:{row.id}:{row.version}",
        )
    )
