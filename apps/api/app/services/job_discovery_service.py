from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.browser.job_discovery import DiscoveredJob, JobDiscoveryBatch
from adapters.llm.errors import LlmProviderError
from apps.api.app.core.config import get_settings
from apps.api.app.models import entities as db
from apps.api.app.schemas.job import JobImportPayload
from apps.api.app.schemas.score import ScoreRequest
from apps.api.app.services.action_service import PREWRITE_RETRYABLE_FAILURES
from apps.api.app.services.automation_service import (
    _effective_rules,
    dispatch_proactive_greeting,
)
from apps.api.app.services.conversation_service import create_greeting_draft
from apps.api.app.services.job_service import import_job
from apps.api.app.services.llm_circuit_service import open_llm_circuit
from apps.api.app.services.llm_config_service import runtime_settings
from apps.api.app.services.score_service import create_score
from packages.browser_worker.actions import ActionExecutor
from packages.llm.ports import LlmProvider
from packages.scoring.llm_engine import LlmScoreValidationError

RETRYABLE_LLM_CODES = {
    "LLM_NOT_CONFIGURED",
    "LLM_AUTHENTICATION_FAILED",
    "LLM_RATE_LIMITED",
    "LLM_TIMEOUT",
    "LLM_NETWORK_ERROR",
    "LLM_SERVICE_ERROR",
}


def process_job_discovery_batch(
    session: Session,
    run: db.AgentRun,
    batch: JobDiscoveryBatch,
    *,
    provider: LlmProvider,
    executor: ActionExecutor,
    cdp_url: str,
    now: datetime | None = None,
) -> dict[str, int]:
    if run.platform != batch.platform.value:
        raise ValueError("职位发现批次平台与 Agent 运行不匹配")
    current = now or datetime.now(UTC)
    rules = _effective_rules(session, run.platform, run.strategy_id)
    blocked = job_scan_block_reasons(session, run, rules, current)
    if blocked:
        _event(session, run, "JOB_SCAN_BLOCKED", blocked)
        session.commit()
        return {"discovered": len(batch.items), "scored": 0, "contacted": 0, "skipped": len(batch.items)}
    strategy = session.get(db.JobStrategy, run.strategy_id)
    if strategy is None:
        raise ValueError("Agent 策略不存在")
    counts = {"discovered": len(batch.items), "scored": 0, "contacted": 0, "skipped": 0}
    retry_backoff_until: datetime | None = None
    for index, item in enumerate(batch.items):
        _state_event(session, run, item, "READING_CARD")
        record = _record_for_item(session, run, item)
        if record.status not in {"DISCOVERED", "RETRYABLE"}:
            counts["skipped"] += 1
            continue
        _state_event(session, run, item, "OPENING_DETAIL")
        _state_event(session, run, item, "VERIFYING_JOB")
        if item.detail is None or item.detail.job is None or item.reason_codes:
            reasons = item.reason_codes or ["JOB_DETAIL_MISSING"]
            retryable_reason = next(
                (
                    reason
                    for reason in reasons
                    if reason in {"JOB_DETAIL_OPEN_FAILED", "JOB_DETAIL_NOT_READY"}
                ),
                None,
            )
            if retryable_reason:
                retry_backoff_until = _schedule_retry(
                    record, retryable_reason, current
                )
            else:
                _finish(record, "SKIPPED", reasons)
            counts["skipped"] += 1
            if retry_backoff_until is not None:
                counts["skipped"] += len(batch.items[index + 1:])
                break
            continue
        source = item.detail.job
        safety = _job_safety_reasons(source)
        if safety:
            _finish(record, "SKIPPED", safety)
            counts["skipped"] += 1
            continue
        _state_event(session, run, item, "IMPORTING")
        imported = import_job(
            session,
            JobImportPayload(
                external_job_id=source.external_job_id,
                title=source.title,
                company_name=source.company_name,
                industry=source.industry,
                location=source.location,
                work_mode=source.work_mode,
                salary_text=source.salary_text,
                description=source.description,
                source_status=source.source_status,
                source=batch.platform.value,
            ),
        )
        job = session.get(db.Job, imported.job.id)
        if job is None:
            raise RuntimeError("职位导入后无法读取")
        record.job_id = job.id
        record.content_hash = job.content_hash
        duplicate = _duplicate_reason(session, job)
        if duplicate:
            _finish(record, "SKIPPED", [duplicate])
            counts["skipped"] += 1
            continue
        cooldown = _cooldown_reason(
            session,
            job.id,
            job.company_name,
            source.recruiter_name,
            rules,
            current,
        )
        if cooldown:
            _finish(record, "SKIPPED", [cooldown])
            counts["skipped"] += 1
            continue
        try:
            _state_event(session, run, item, "PARSING")
            _state_event(session, run, item, "SCORING")
            score = create_score(
                session,
                job.id,
                ScoreRequest(
                    strategy_id=run.strategy_id,
                    candidate_profile_id=strategy.candidate_profile_id,
                ),
                provider=provider,
            )
            record.job_score_id = score.id
            if score.hard_rejected:
                _finish(
                    record,
                    "SKIPPED",
                    [
                        item.rule_code
                        for item in score.rejection_reasons
                    ] or ["HARD_FILTERED"],
                )
                counts["skipped"] += 1
                continue
            counts["scored"] += 1
            draft = create_greeting_draft(session, score.id, provider)
            _state_event(session, run, item, "AUTHORIZING")
            _state_event(session, run, item, "CONTACTING")
            result = dispatch_proactive_greeting(
                session,
                job.id,
                draft.id,
                source.recruiter_name or "",
                cdp_url,
                executor=executor,
                agent_run_id=run.id,
                platform=batch.platform.value,
            )
        except LlmProviderError as exc:
            if exc.code in RETRYABLE_LLM_CODES:
                open_llm_circuit(
                    session,
                    runtime_settings(session),
                    exc.code,
                    now=current,
                )
                record.status = "RETRYABLE"
                record.reason_codes = [exc.code, "WAITING_FOR_LLM_RECOVERY"]
                record.next_retry_at = current
                retry_backoff_until = current
                deferred_ids = {
                    deferred.summary.external_job_id
                    for deferred in batch.items[index:]
                }
                batch.seen_job_ids = [
                    external_id
                    for external_id in batch.seen_job_ids
                    if external_id not in deferred_ids
                ]
            else:
                _finish(record, "SKIPPED", [exc.code])
            counts["skipped"] += 1
            if retry_backoff_until is not None:
                counts["skipped"] += len(batch.items[index + 1:])
                break
            continue
        except LlmScoreValidationError:
            _schedule_retry(record, "INVALID_SCORING_OUTPUT", current)
            counts["skipped"] += 1
            continue
        record.action_id = (
            UUID(str(result["action_id"])) if result.get("action_id") else None
        )
        action_status = result.get("action_status")
        if action_status == "SUCCEEDED":
            _finish(record, "CONTACTED", ["GREETING_SENT"])
            counts["contacted"] += 1
        elif action_status == "FAILED_RETRYABLE":
            _schedule_retry(
                record,
                str(result.get("failure_code") or "GREETING_PREWRITE_FAILED"),
                current,
            )
            counts["skipped"] += 1
        else:
            raw_reasons = result.get("reason_codes")
            _finish(
                record,
                "SKIPPED",
                (
                    [str(code) for code in raw_reasons]
                    if isinstance(raw_reasons, list)
                    else ["GREETING_NOT_SENT"]
                ),
            )
            counts["skipped"] += 1
        _state_event(session, run, item, "RETURNING")
    root_cursor = dict(run.cursor or {})
    root_cursor["job_discovery"] = {
        "search_key": batch.next_search_key or batch.search_key,
        "scroll_position": 0 if batch.exhausted else batch.scroll_position,
        "next_cursor": batch.next_cursor,
        "seen_job_ids": batch.seen_job_ids,
        "last_scan_at": batch.scanned_at.isoformat(),
        "next_scan_at": (
            retry_backoff_until or batch.next_scan_at
        ).isoformat(),
        "exhausted": batch.exhausted,
        # 保留字段兼容既有游标，但 BOSS 常驻页面不得主动刷新。
        "refresh_before_scan": False,
        "switch_search_before_scan": batch.exhausted,
    }
    run.cursor = root_cursor
    _event(session, run, "JOB_SCAN_COMPLETED", [f"{key.upper()}_{value}" for key, value in counts.items()])
    session.commit()
    return counts


def job_scan_block_reasons(
    session: Session, run: db.AgentRun, rules: object, now: datetime
) -> list[str]:
    from packages.policy_engine.automation import AutomationRules

    assert isinstance(rules, AutomationRules)
    if rules.emergency_stop:
        return ["EMERGENCY_STOP_ACTIVE"]
    if not rules.enabled or rules.paused or not rules.job_scan_enabled:
        return ["JOB_SCAN_DISABLED_OR_PAUSED"]
    raw_cursor = (run.cursor or {}).get("job_discovery")
    if isinstance(raw_cursor, dict):
        raw_next = raw_cursor.get("next_scan_at")
        if isinstance(raw_next, str):
            next_scan = datetime.fromisoformat(raw_next)
            if next_scan > now:
                return ["NEXT_SCAN_NOT_DUE"]
    local_hour = now.astimezone(ZoneInfo("Asia/Shanghai")).hour
    if not rules.work_start_hour <= local_hour < rules.work_end_hour:
        return ["OUTSIDE_WORKING_HOURS"]
    return []


def next_retryable_job(
    session: Session,
    run: db.AgentRun,
    *,
    now: datetime | None = None,
) -> db.JobDiscoveryRecord | None:
    """只选择队首的一条重试记录，保证 LLM 重试单飞。"""
    current = now or datetime.now(UTC)
    return session.scalar(
        select(db.JobDiscoveryRecord)
        .where(
            db.JobDiscoveryRecord.agent_run_id == run.id,
            db.JobDiscoveryRecord.status == "RETRYABLE",
            db.JobDiscoveryRecord.next_retry_at.is_not(None),
            db.JobDiscoveryRecord.next_retry_at <= current,
        )
        .order_by(
            db.JobDiscoveryRecord.next_retry_at.asc().nullsfirst(),
            db.JobDiscoveryRecord.created_at.asc(),
            db.JobDiscoveryRecord.id.asc(),
        )
        .limit(1)
    )


def _record_for_item(
    session: Session, run: db.AgentRun, item: DiscoveredJob
) -> db.JobDiscoveryRecord:
    existing = session.scalar(
        select(db.JobDiscoveryRecord).where(
            db.JobDiscoveryRecord.agent_run_id == run.id,
            db.JobDiscoveryRecord.external_job_id == item.summary.external_job_id,
        )
    )
    if existing:
        return existing
    record = db.JobDiscoveryRecord(
        agent_run_id=run.id,
        external_job_id=item.summary.external_job_id,
        company_name=item.summary.company_name,
        job_title=item.summary.title,
        recruiter_name=(
            item.detail.job.recruiter_name
            if item.detail and item.detail.job
            else None
        ),
        status="DISCOVERED",
    )
    session.add(record)
    session.flush()
    return record


def _job_safety_reasons(job: object) -> list[str]:
    from packages.browser_worker.models import BrowserJob

    assert isinstance(job, BrowserJob)
    reasons: list[str] = []
    if job.source_status != "OPEN":
        reasons.append("JOB_NOT_OPEN")
    if not job.external_job_id:
        reasons.append("EXTERNAL_JOB_ID_MISSING")
    if not job.company_name or job.company_name in {"匿名公司", "某公司", "保密"}:
        reasons.append("ANONYMOUS_COMPANY")
    if job.work_mode == "UNKNOWN":
        reasons.append("WORK_MODE_UNKNOWN")
    if not job.recruiter_name:
        reasons.append("RECRUITER_UNKNOWN")
    return reasons


def _duplicate_reason(session: Session, job: db.Job) -> str | None:
    existing_action = session.scalar(
        select(db.ActionQueue).where(
            db.ActionQueue.action_type == "GREETING",
            db.ActionQueue.job_id == job.id,
        )
    )
    if existing_action and not _is_prewrite_retryable(existing_action):
        return "JOB_ALREADY_CONTACTED"
    if session.scalar(
        select(db.Conversation.id).where(db.Conversation.job_id == job.id)
    ):
        return "JOB_ALREADY_HAS_CONVERSATION"
    contacted = session.scalars(
        select(db.Job)
        .join(db.ActionQueue, db.ActionQueue.job_id == db.Job.id)
        .where(
            db.ActionQueue.action_type == "GREETING",
            db.ActionQueue.status.in_(
                ["APPROVED", "EXECUTING", "SUCCEEDED", "OUTCOME_UNKNOWN"]
            ),
            db.Job.id != job.id,
        )
    ).all()
    for other in contacted:
        if other.company_name == job.company_name and other.title == job.title:
            return "SAME_COMPANY_TITLE_ALREADY_CONTACTED"
        if SequenceMatcher(None, other.description, job.description).ratio() >= 0.90:
            return "SIMILAR_JD_ALREADY_CONTACTED"
    return None


def _is_prewrite_retryable(action: db.ActionQueue) -> bool:
    return (
        action.status == "FAILED_RETRYABLE"
        and action.failure_code in PREWRITE_RETRYABLE_FAILURES
    )


def _cooldown_reason(
    session: Session,
    job_id: UUID,
    company: str,
    recruiter: str | None,
    rules: object,
    now: datetime,
) -> str | None:
    from packages.policy_engine.automation import AutomationRules

    assert isinstance(rules, AutomationRules)
    if rules.company_cooldown_hours and session.scalar(
        select(db.ActionQueue.id).where(
            db.ActionQueue.action_type == "GREETING",
            db.ActionQueue.status.in_(
                ["APPROVED", "EXECUTING", "SUCCEEDED", "OUTCOME_UNKNOWN"]
            ),
            db.ActionQueue.job_id != job_id,
            db.ActionQueue.target_company == company,
            db.ActionQueue.created_at
            >= now - timedelta(hours=rules.company_cooldown_hours),
        )
    ):
        return "COMPANY_COOLDOWN_ACTIVE"
    if recruiter and rules.recruiter_cooldown_hours and session.scalar(
        select(db.ActionQueue.id).where(
            db.ActionQueue.action_type == "GREETING",
            db.ActionQueue.status.in_(
                ["APPROVED", "EXECUTING", "SUCCEEDED", "OUTCOME_UNKNOWN"]
            ),
            db.ActionQueue.job_id != job_id,
            db.ActionQueue.target_recruiter == recruiter,
            db.ActionQueue.created_at
            >= now - timedelta(hours=rules.recruiter_cooldown_hours),
        )
    ):
        return "RECRUITER_COOLDOWN_ACTIVE"
    return None


def _finish(
    record: db.JobDiscoveryRecord, status: str, reasons: list[str]
) -> None:
    record.status = status
    record.reason_codes = reasons
    if status != "RETRYABLE":
        record.next_retry_at = None


def _schedule_retry(
    record: db.JobDiscoveryRecord,
    reason_code: str,
    now: datetime,
) -> datetime | None:
    settings = get_settings()
    record.retry_count += 1
    if record.retry_count >= settings.boss_job_retry_max_attempts:
        record.status = "SKIPPED"
        record.reason_codes = [reason_code, "RETRY_ATTEMPTS_EXHAUSTED"]
        record.next_retry_at = None
        return None
    delay_seconds = min(
        settings.boss_llm_retry_base_seconds * (2 ** (record.retry_count - 1)),
        settings.boss_llm_retry_max_seconds,
    )
    record.status = "RETRYABLE"
    record.reason_codes = [reason_code]
    record.next_retry_at = now + timedelta(seconds=delay_seconds)
    return record.next_retry_at


def mark_retry_target_not_visible(
    session: Session,
    record: db.JobDiscoveryRecord,
    *,
    now: datetime | None = None,
) -> None:
    """重试目标不在当前列表时也推进退避，防止无限切换职位入口。"""
    _schedule_retry(
        record,
        "JOB_RETRY_TARGET_NOT_VISIBLE",
        now or datetime.now(UTC),
    )
    session.commit()


def _event(
    session: Session,
    run: db.AgentRun,
    event_type: str,
    reasons: list[str],
) -> None:
    session.add(
        db.AgentRunEvent(
            agent_run_id=run.id,
            event_type=event_type,
            entity_type="job_discovery",
            reason_codes=reasons,
        )
    )


def _state_event(
    session: Session,
    run: db.AgentRun,
    item: DiscoveredJob,
    state: str,
) -> None:
    session.add(
        db.AgentRunEvent(
            agent_run_id=run.id,
            event_type="JOB_DISCOVERY_STATE_CHANGED",
            entity_type="job",
            reason_codes=[state],
            metadata_json={"external_job_id": item.summary.external_job_id},
        )
    )
