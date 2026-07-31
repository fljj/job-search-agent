from datetime import UTC, datetime, timedelta
from urllib.error import URLError
from urllib.request import urlopen

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from adapters.browser.playwright_actions import PlaywrightActionExecutor
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.models import entities as db
from apps.api.app.services.action_service import observe_action, reconcile_action
from apps.api.app.services.llm_circuit_service import llm_circuit_status
from apps.api.app.services.llm_config_service import runtime_settings
from apps.api.app.services.user_service import DEFAULT_USER_ID


def register_worker(
    session: Session,
    worker_id: str,
    hostname: str,
    pid: int,
    *,
    metadata: dict[str, object] | None = None,
    now: datetime | None = None,
) -> db.WorkerInstance:
    current = now or datetime.now(UTC)
    stale_before = current - timedelta(seconds=get_settings().worker_stale_seconds)
    active = session.scalars(
        select(db.WorkerInstance)
        .where(db.WorkerInstance.status == "RUNNING")
        .with_for_update()
    ).all()
    for item in active:
        if item.worker_id == worker_id:
            item.heartbeat_at = current
            session.commit()
            return item
        if item.heartbeat_at >= stale_before:
            raise RuntimeError("已有健康的 Agent Worker 正在运行")
        item.status = "STALE"
        item.stopped_at = current
    existing = session.scalar(
        select(db.WorkerInstance).where(db.WorkerInstance.worker_id == worker_id)
    )
    if existing:
        existing.hostname = hostname
        existing.pid = pid
        existing.status = "RUNNING"
        existing.started_at = current
        existing.heartbeat_at = current
        existing.stopped_at = None
        existing.metadata_json = metadata or {}
        worker = existing
    else:
        worker = db.WorkerInstance(
            worker_id=worker_id,
            hostname=hostname,
            pid=pid,
            status="RUNNING",
            started_at=current,
            heartbeat_at=current,
            metadata_json=metadata or {},
        )
        session.add(worker)
    session.commit()
    session.refresh(worker)
    return worker


def heartbeat_worker(
    session: Session, worker_id: str, now: datetime | None = None
) -> None:
    worker = session.scalar(
        select(db.WorkerInstance).where(db.WorkerInstance.worker_id == worker_id)
    )
    if worker is None or worker.status != "RUNNING":
        raise RuntimeError("Worker 实例未登记或已经停止")
    worker.heartbeat_at = now or datetime.now(UTC)
    session.commit()


def stop_worker(session: Session, worker_id: str) -> None:
    worker = session.scalar(
        select(db.WorkerInstance).where(db.WorkerInstance.worker_id == worker_id)
    )
    if worker is None:
        return
    worker.status = "STOPPED"
    worker.stopped_at = datetime.now(UTC)
    session.commit()


def enqueue_unknown_actions(
    session: Session, now: datetime | None = None
) -> int:
    current = now or datetime.now(UTC)
    timeout = timedelta(minutes=get_settings().reconciliation_timeout_minutes)
    actions = session.scalars(
        select(db.ActionQueue).where(
            db.ActionQueue.user_id == DEFAULT_USER_ID,
            db.ActionQueue.status == "OUTCOME_UNKNOWN",
            ~select(db.ReconciliationTask.id)
            .where(db.ReconciliationTask.action_id == db.ActionQueue.id)
            .exists(),
        )
    ).all()
    for action in actions:
        session.add(
            db.ReconciliationTask(
                action_id=action.id,
                status="PENDING",
                next_attempt_at=current,
                deadline_at=current + timeout,
            )
        )
    session.commit()
    return len(actions)


def process_reconciliation_queue(
    session: Session,
    cdp_url: str,
    *,
    observer: PlaywrightActionExecutor | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or datetime.now(UTC)
    enqueue_unknown_actions(session, current)
    tasks = session.scalars(
        select(db.ReconciliationTask)
        .where(
            db.ReconciliationTask.status == "PENDING",
            db.ReconciliationTask.next_attempt_at <= current,
        )
        .order_by(db.ReconciliationTask.created_at)
        .limit(get_settings().reconciliation_batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    counts = {"processed": 0, "resolved": 0, "manual_required": 0}
    for task in tasks:
        task_id = task.id
        task.status = "IN_PROGRESS"
        task.attempt_count += 1
        try:
            result = reconcile_action(
                session, task.action_id, cdp_url, observer
            )
        except (OSError, RuntimeError, ValueError) as exc:
            session.rollback()
            refreshed = session.get(db.ReconciliationTask, task_id)
            if refreshed is None:
                continue
            refreshed.last_error_code = type(exc).__name__
            _reschedule_or_escalate(refreshed, current)
            session.commit()
        else:
            refreshed = session.get(db.ReconciliationTask, task_id)
            if refreshed is None:
                continue
            if result.status != "OUTCOME_UNKNOWN":
                refreshed.status = "RESOLVED"
                refreshed.resolved_at = current
                counts["resolved"] += 1
            else:
                refreshed.last_error_code = result.failure_code
                _reschedule_or_escalate(refreshed, current)
            session.commit()
        counts["processed"] += 1
        if refreshed.status == "MANUAL_REQUIRED":
            counts["manual_required"] += 1
    return counts


def list_reconciliation_tasks(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(
        select(db.ReconciliationTask)
        .order_by(db.ReconciliationTask.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": row.id,
            "action_id": row.action_id,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "next_attempt_at": row.next_attempt_at.isoformat(),
            "deadline_at": row.deadline_at.isoformat(),
            "last_error_code": row.last_error_code,
        }
        for row in rows
    ]


def audit_discrepancies(session: Session) -> list[dict[str, object]]:
    discrepancies: list[dict[str, object]] = []
    succeeded = session.scalars(
        select(db.ActionQueue).where(db.ActionQueue.status == "SUCCEEDED")
    ).all()
    for action in succeeded:
        successful_attempt = session.scalar(
            select(db.ActionAttempt.id).where(
                db.ActionAttempt.action_id == action.id,
                db.ActionAttempt.status == "SUCCEEDED",
            )
        )
        recommendation_source = session.scalar(
            select(db.PlatformRecommendation.id).where(
                db.PlatformRecommendation.action_id == action.id
            )
        )
        if not successful_attempt or (
            not action.policy_decision_id and not recommendation_source
        ):
            discrepancies.append(
                {
                    "code": "SUCCESS_WITHOUT_PROVENANCE",
                    "action_id": action.id,
                }
            )
    unknown_without_task = session.scalars(
        select(db.ActionQueue.id).where(
            db.ActionQueue.status == "OUTCOME_UNKNOWN",
            ~select(db.ReconciliationTask.id)
            .where(db.ReconciliationTask.action_id == db.ActionQueue.id)
            .exists(),
        )
    ).all()
    discrepancies.extend(
        {"code": "UNKNOWN_WITHOUT_RECONCILIATION", "action_id": action_id}
        for action_id in unknown_without_task
    )
    return discrepancies


def verify_successful_actions(
    session: Session,
    cdp_url: str,
    *,
    observer: PlaywrightActionExecutor | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    discrepancies: list[dict[str, object]] = []
    actions = session.scalars(
        select(db.ActionQueue)
        .where(db.ActionQueue.status == "SUCCEEDED")
        .order_by(db.ActionQueue.finished_at.desc())
        .limit(limit)
    ).all()
    for action in actions:
        result = observe_action(session, action.id, cdp_url, observer)
        if (
            result.outcome.value == "FAILED_RETRYABLE"
            and result.error_code == "RESULT_CONFIRMED_NOT_SENT"
        ):
            discrepancy = {
                "code": "PLATFORM_MISSING_DATABASE_SUCCESS",
                "action_id": action.id,
            }
            discrepancies.append(discrepancy)
            exists = session.scalar(
                select(db.AuditEvent.id).where(
                    db.AuditEvent.event_type == "AUDIT_DISCREPANCY_DETECTED",
                    db.AuditEvent.entity_id == action.id,
                )
            )
            if not exists:
                session.add(
                    db.AuditEvent(
                        user_id=DEFAULT_USER_ID,
                        actor_type="SYSTEM",
                        event_type="AUDIT_DISCREPANCY_DETECTED",
                        entity_type="action",
                        entity_id=action.id,
                        before_state="SUCCEEDED",
                        after_state="REVIEW_REQUIRED",
                        reason_codes=[str(discrepancy["code"])],
                        metadata_json={},
                        correlation_id=f"audit:{action.id}",
                    )
                )
    session.commit()
    return discrepancies


def operations_status(session: Session) -> dict[str, object]:
    settings = runtime_settings(session)
    now = datetime.now(UTC)
    try:
        with session.begin_nested():
            revision = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        database_ready = bool(revision)
    except SQLAlchemyError:
        revision = None
        database_ready = False
    stale_before = now - timedelta(seconds=settings.worker_stale_seconds)
    workers = session.scalars(
        select(db.WorkerInstance).order_by(db.WorkerInstance.started_at.desc()).limit(20)
    ).all()
    runs = session.scalars(
        select(db.AgentRun).where(
            db.AgentRun.user_id == DEFAULT_USER_ID,
            db.AgentRun.status.in_(["RUNNING", "PAUSED"]),
        )
    ).all()
    platform_sessions = session.scalars(
        select(db.PlatformSession).where(db.PlatformSession.user_id == DEFAULT_USER_ID)
    ).all()
    return {
        "database_ready": database_ready,
        "migration_revision": revision,
        "llm_configured": settings.llm_configured,
        "llm_circuit": llm_circuit_status(session, settings),
        "selector_version": get_browser_selectors().version,
        "executor_mode": settings.agent_executor_mode,
        "calendar_provider": settings.calendar_provider,
        "retention": {
            "audit_days": settings.audit_retention_days,
            "run_event_days": settings.run_event_retention_days,
        },
        "workers": [
            {
                "worker_id": item.worker_id,
                "status": (
                    "STALE"
                    if item.status == "RUNNING" and item.heartbeat_at < stale_before
                    else item.status
                ),
                "pid": item.pid,
                "hostname": item.hostname,
                "heartbeat_at": item.heartbeat_at.isoformat(),
            }
            for item in workers
        ],
        "desired_runs": [
            {"platform": item.platform, "desired_state": item.status}
            for item in runs
        ],
        "platform_readiness": [
            {
                "platform": item.platform,
                "status": item.status,
                "reason_codes": item.last_reason_codes,
                "last_checked_at": (
                    item.last_checked_at.isoformat() if item.last_checked_at else None
                ),
            }
            for item in platform_sessions
        ],
        "capabilities": {
            "llm": llm_circuit_status(session, settings)["status"],
            "calendar": "CONFIGURED" if settings.calendar_provider else "UNAVAILABLE",
            "executor": "CONFIGURED" if settings.agent_executor_mode in {"REAL", "FAKE"} else "UNAVAILABLE",
        },
        "unknown_action_count": _count_actions(session, "OUTCOME_UNKNOWN"),
        "pending_confirmation_count": session.scalar(
            select(func.count())
            .select_from(db.ConfirmationTask)
            .where(db.ConfirmationTask.status == "PENDING_APPROVAL")
        )
        or 0,
        "reconciliation_tasks": list_reconciliation_tasks(session),
        "discrepancies": audit_discrepancies(session),
    }


def apply_retention(
    session: Session, now: datetime | None = None
) -> dict[str, int]:
    current = now or datetime.now(UTC)
    settings = get_settings()
    run_result = session.execute(
        delete(db.AgentRunEvent).where(
            db.AgentRunEvent.created_at
            < current - timedelta(days=settings.run_event_retention_days)
        )
    )
    audit_result = session.execute(
        delete(db.AuditEvent).where(
            db.AuditEvent.occurred_at
            < current - timedelta(days=settings.audit_retention_days)
        )
    )
    session.commit()
    return {
        "run_events_deleted": int(getattr(run_result, "rowcount", 0) or 0),
        "audit_events_deleted": int(getattr(audit_result, "rowcount", 0) or 0),
    }


def startup_preflight(session: Session, settings: Settings) -> list[str]:
    settings = runtime_settings(session, settings)
    reasons: list[str] = []
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        reasons.append("DATABASE_UNAVAILABLE")
    if settings.llm_provider != "FAKE" and not settings.llm_configured:
        reasons.append("LLM_NOT_CONFIGURED")
    try:
        with session.begin_nested():
            revision = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        if not revision:
            reasons.append("DATABASE_MIGRATION_MISSING")
    except SQLAlchemyError:
        reasons.append("DATABASE_MIGRATION_MISSING")
    if not get_browser_selectors().version:
        reasons.append("SELECTOR_VERSION_MISSING")
    if settings.agent_executor_mode not in {"REAL", "FAKE"}:
        reasons.append("EXECUTOR_MODE_INVALID")
    return reasons


def worker_preflight(
    session: Session, settings: Settings, cdp_url: str
) -> list[str]:
    reasons = startup_preflight(session, settings)
    has_real_run = bool(
        session.scalar(
            select(db.AgentRun.id).where(
                db.AgentRun.status == "RUNNING",
                db.AgentRun.platform != "MOCK",
            )
        )
    )
    if not has_real_run:
        return reasons
    if settings.agent_executor_mode != "REAL":
        reasons.append("REAL_RUN_REQUIRES_REAL_EXECUTOR")
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=3):
            pass
    except (OSError, URLError, TimeoutError):
        reasons.append("CDP_UNAVAILABLE")
    # 初始化后的首个消息列表扫描负责建立 SESSION_READY 证据；tick 仍会强制复核该状态。
    return reasons


def _reschedule_or_escalate(
    task: db.ReconciliationTask, now: datetime
) -> None:
    if now >= task.deadline_at:
        task.status = "MANUAL_REQUIRED"
        return
    task.status = "PENDING"
    delay_seconds = min(300, 2 ** min(task.attempt_count, 8))
    task.next_attempt_at = now + timedelta(seconds=delay_seconds)


def _count_actions(session: Session, status: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(db.ActionQueue)
            .where(db.ActionQueue.status == status)
        )
        or 0
    )
