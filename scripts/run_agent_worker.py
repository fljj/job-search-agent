"""单实例短轮询 Worker；真实平台使用本机 CDP，MOCK 保持离线执行。"""

import fcntl
import hashlib
import logging
import os
import signal
import socket
import threading
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.job_discovery import (
    BossJobDiscoveryAdapter,
    DiscoveredJob,
    JobDiscoveryBatch,
    is_obviously_irrelevant_title,
    is_potentially_relevant_title,
)
from adapters.browser.message_discovery import (
    BossMessageDiscoveryAdapter,
    MaimaiMessageDiscoveryAdapter,
    MessageDiscoveryAdapter,
)
from adapters.browser.playwright_actions import PlaywrightActionExecutor
from adapters.browser.telegram_jobs import TelegramJobDiscoveryAdapter
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.config import get_settings, reload_settings
from apps.api.app.core.database import SessionLocal
from apps.api.app.core.job_parser_config import get_job_parser_config
from apps.api.app.core.recommendation_config import get_recommendation_rules
from apps.api.app.core.telegram_config import get_telegram_policy
from apps.api.app.models import entities as db
from apps.api.app.services.agent_service import pause_run, tick_run
from apps.api.app.services.automation_service import _effective_rules
from apps.api.app.services.job_discovery_service import (
    job_scan_block_reasons,
    mark_retry_target_not_visible,
    next_retryable_job,
    process_job_discovery_batch,
)
from apps.api.app.services.llm_circuit_service import (
    llm_circuit_is_open,
    probe_llm_circuit,
)
from apps.api.app.services.llm_config_service import (
    build_runtime_llm_provider,
    runtime_settings,
)
from apps.api.app.services.message_discovery_service import (
    execute_pending_platform_consents,
    persist_discovery_batch,
    process_next_inbound_job_score,
    record_ready_platform_session,
)
from apps.api.app.services.operations_service import (
    apply_retention,
    heartbeat_worker,
    process_reconciliation_queue,
    register_worker,
    stop_worker,
    worker_preflight,
)
from apps.api.app.services.recommendation_service import (
    dispatch_recommendation,
    scan_recommendations,
)
from packages.audit.redaction import install_redacting_filter
from packages.audit.runtime_logging import configure_runtime_logging, runtime_event
from packages.browser_worker.actions import ActionExecutor
from packages.browser_worker.models import PageType, Platform, ReadResult, SessionStatus

logger = logging.getLogger(__name__)
LOCK_PATH = "/tmp/job-search-agent-worker.lock"
STOP_EVENT = threading.Event()
MESSAGE_DISCOVERY_PAUSE_AFTER_FAILURES = 3


def _merge_seen_job_ids(
    cursor_items: object,
    persisted_items: Sequence[object],
    excluded_items: Collection[object] = (),
) -> list[str]:
    cursor_values = cursor_items if isinstance(cursor_items, list) else []
    excluded = {str(item) for item in excluded_items}
    return list(
        dict.fromkeys(
            [
                *(str(item) for item in cursor_values if str(item) not in excluded),
                *(str(item) for item in persisted_items if str(item) not in excluded),
            ]
        )
    )


def _send_worker_heartbeat(worker_id: str) -> None:
    with SessionLocal() as session:
        heartbeat_worker(session, worker_id)


def _heartbeat_loop(
    worker_id: str,
    interval_seconds: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(interval_seconds):
        try:
            _send_worker_heartbeat(worker_id)
        except Exception:
            logger.exception("Worker heartbeat failed; will retry safely")


def _build_executor(platform: str, mode: str) -> tuple[ActionExecutor, str]:
    if platform in {
        Platform.BOSS.value,
        Platform.MAIMAI.value,
        Platform.TELEGRAM.value,
    }:
        if mode != "REAL":
            raise ValueError("真实招聘平台正式运行禁止使用 Fake 执行器")
        return PlaywrightActionExecutor(get_browser_selectors()), "REAL_CDP"
    if platform == "MOCK":
        if mode != "FAKE":
            raise ValueError("MOCK 运行必须显式配置 Fake 执行器")
        return FakeActionExecutor(), "FAKE"
    raise ValueError(f"平台 {platform} 尚无正式执行器")


def _discover_messages(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    cdp_url: str,
    adapter: MessageDiscoveryAdapter,
    executor: ActionExecutor | None = None,
) -> bool:
    raw_cursor = (run.cursor or {}).get("message_discovery")
    cursor = raw_cursor if isinstance(raw_cursor, dict) else {}
    raw_position = cursor.get("scroll_position")
    raw_seen = cursor.get("seen_message_keys")
    terminal_conversation_ids = list(
        session.scalars(
            select(db.Conversation.external_conversation_id).where(
                db.Conversation.platform == run.platform,
                db.Conversation.state.in_(["ENDED", "DECLINED", "PAUSED", "OUTCOME_UNKNOWN"]),
            )
        ).all()
    )
    known_linked_job_ids = {
        str(conversation_id): str(external_job_id)
        for conversation_id, external_job_id in session.execute(
            select(
                db.Conversation.external_conversation_id,
                db.Conversation.observed_external_job_id,
            ).where(
                db.Conversation.platform == run.platform,
                db.Conversation.job_id.is_not(None),
                db.Conversation.observed_external_job_id.is_not(None),
            )
        ).all()
    }
    try:
        batch = adapter.scan(
            cdp_url,
            partition="ALL",
            scroll_position=raw_position if isinstance(raw_position, int) else 0,
            seen_message_keys=[
                str(item) for item in (raw_seen if isinstance(raw_seen, list) else [])
            ],
            excluded_conversation_ids=terminal_conversation_ids,
            known_linked_job_ids=known_linked_job_ids,
            limit=get_settings().agent_tick_batch_size,
        )
    except (OSError, TimeoutError, ValueError) as exc:
        if isinstance(adapter, BossMessageDiscoveryAdapter):
            try:
                reopened = adapter.ensure_list_page(cdp_url)
            except (OSError, TimeoutError, ValueError):
                reopened = False
            if reopened:
                runtime_event(
                    logger,
                    "PLATFORM_PAGE_REOPENED",
                    worker_id=worker_id,
                    run_id=run.id,
                    platform=run.platform,
                    page_type="MESSAGE_LIST",
                )
                session.commit()
                return False
        root_cursor = dict(run.cursor or {})
        raw_health = root_cursor.get("message_discovery_health")
        health = raw_health if isinstance(raw_health, dict) else {}
        failure_count = int(health.get("consecutive_failure_count") or 0) + 1
        root_cursor["message_discovery_health"] = {
            "consecutive_failure_count": failure_count,
            "last_failure_at": datetime.now(UTC).isoformat(),
            "last_error_type": type(exc).__name__,
        }
        run.cursor = root_cursor
        runtime_event(
            logger,
            "MESSAGE_DISCOVERY_FAILED",
            worker_id=worker_id,
            run_id=run.id,
            platform=run.platform,
            error_type=type(exc).__name__,
            failure_count=failure_count,
        )
        if failure_count >= MESSAGE_DISCOVERY_PAUSE_AFTER_FAILURES:
            pause_run(session, run.id, ["MESSAGE_DISCOVERY_UNAVAILABLE"])
        else:
            session.commit()
        return False
    root_cursor = dict(run.cursor or {})
    if root_cursor.pop("message_discovery_health", None) is not None:
        run.cursor = root_cursor
    record_ready_platform_session(session, run, cdp_url)
    counts = persist_discovery_batch(session, run, worker_id, batch)
    consent_statuses = (
        execute_pending_platform_consents(session, run, cdp_url, executor)
        if executor is not None
        else []
    )
    inbound_score_status = (
        process_next_inbound_job_score(
            session,
            run,
            build_runtime_llm_provider(session),
        )
        if executor is not None
        else "SKIPPED"
    )
    runtime_event(
        logger,
        "MESSAGE_SCAN_COMPLETED",
        worker_id=worker_id,
        run_id=run.id,
        platform=run.platform,
        scanned_count=len(batch.items),
        imported_count=counts["imported"],
        paused_count=counts["paused"],
        inbound_score_status=inbound_score_status,
        consent_action_statuses=consent_statuses,
        exhausted=batch.exhausted,
        cursor=batch.scroll_position,
    )
    return True


def _process_maimai_recommendations(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    cdp_url: str,
    executor: ActionExecutor,
) -> bool:
    rules = _effective_rules(session, run.platform, run.strategy_id)
    if (
        not rules.enabled
        or rules.paused
        or rules.emergency_stop
        or not rules.maimai_recommendation_enabled
    ):
        return True
    try:
        recommendations = scan_recommendations(
            session,
            run,
            cdp_url,
            limit=get_settings().agent_tick_batch_size,
        )
    except (OSError, TimeoutError, ValueError):
        pause_run(
            session,
            run.id,
            ["RECOMMENDATION_DISCOVERY_UNAVAILABLE"],
        )
        return False
    for recommendation in recommendations:
        if recommendation["action_status"] == "APPROVED":
            dispatch_recommendation(
                session,
                UUID(str(recommendation["id"])),
                cdp_url,
                executor=executor,
            )
    runtime_event(
        logger,
        "RECOMMENDATION_SCAN_COMPLETED",
        worker_id=worker_id,
        run_id=run.id,
        scanned_count=len(recommendations),
    )
    return True


@contextmanager
def _single_worker_lock() -> Iterator[None]:
    with open(LOCK_PATH, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有 Agent Worker 正在运行") from exc
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        yield


def _run_boss_job_discovery(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    cdp_url: str,
    executor: ActionExecutor,
    rules: object,
) -> None:
    from packages.policy_engine.automation import AutomationRules

    assert isinstance(rules, AutomationRules)
    raw_job_cursor = (run.cursor or {}).get("job_discovery")
    job_cursor = raw_job_cursor if isinstance(raw_job_cursor, dict) else {}
    raw_job_position = job_cursor.get("scroll_position")
    raw_seen_jobs = job_cursor.get("seen_job_ids")
    retry_record = next_retryable_job(session, run)
    retry_job_id = retry_record.external_job_id if retry_record is not None else None
    persisted_seen_query = (
        select(db.JobDiscoveryRecord.external_job_id)
        .join(
            db.AgentRun,
            db.AgentRun.id == db.JobDiscoveryRecord.agent_run_id,
        )
        .where(
            db.AgentRun.user_id == run.user_id,
            db.AgentRun.platform == run.platform,
        )
    )
    if retry_job_id is not None:
        persisted_seen_query = persisted_seen_query.where(
            db.JobDiscoveryRecord.external_job_id != retry_job_id
        )
    persisted_seen_jobs = session.scalars(persisted_seen_query).all()
    seen_job_ids = _merge_seen_job_ids(
        raw_seen_jobs,
        persisted_seen_jobs,
        [retry_job_id] if retry_job_id else [],
    )
    strategy = session.get(db.JobStrategy, run.strategy_id)
    relevant_title_keywords = (
        [
            rule.pattern
            for rule in strategy.title_rules
            if rule.rule_type == "INCLUDE"
        ]
        if strategy is not None
        else []
    )
    parser_config = get_job_parser_config()
    search_keys = get_settings().boss_job_searches
    adapter = BossJobDiscoveryAdapter(get_browser_selectors())
    try:
        job_batch = adapter.scan(
            cdp_url,
            search_key=str(
                job_cursor.get("search_key")
                or (search_keys[0] if search_keys else "CURRENT_SEARCH")
            ),
            search_keys=search_keys,
            refresh_before_scan=bool(job_cursor.get("refresh_before_scan", False)),
            switch_search_before_scan=bool(
                job_cursor.get(
                    "switch_search_before_scan",
                    not bool(raw_job_cursor),
                )
            ),
            scroll_position=(raw_job_position if isinstance(raw_job_position, int) else 0),
            previous_cursor=(
                str(job_cursor["next_cursor"]) if job_cursor.get("next_cursor") else None
            ),
            seen_job_ids=seen_job_ids,
            target_job_ids={retry_job_id} if retry_job_id else None,
            irrelevant_title_keywords=parser_config.irrelevant_title_keywords,
            relevant_title_keywords=relevant_title_keywords,
            direction_title_keywords=[
                *parser_config.relevant_title_keywords,
                *relevant_title_keywords,
            ],
            limit=1 if retry_job_id else get_settings().boss_job_batch_size,
            interval_seconds=get_settings().boss_job_scan_interval_seconds,
        )
    except (OSError, TimeoutError, ValueError) as exc:
        runtime_event(
            logger,
            "JOB_SCAN_FAILED",
            worker_id=worker_id,
            run_id=run.id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            search_key=str(job_cursor.get("search_key") or ""),
            refresh_before_scan=bool(job_cursor.get("refresh_before_scan", False)),
            switch_search_before_scan=bool(
                job_cursor.get("switch_search_before_scan", not bool(raw_job_cursor))
            ),
        )
        pause_run(session, run.id, ["JOB_DISCOVERY_UNAVAILABLE"])
        return
    try:
        process_job_discovery_batch(
            session,
            run,
            job_batch,
            provider=build_runtime_llm_provider(session),
            executor=executor,
            cdp_url=cdp_url,
        )
        if retry_record is not None and not job_batch.items:
            mark_retry_target_not_visible(session, retry_record)
    finally:
        adapter.close_details(cdp_url, job_batch)
    runtime_event(
        logger,
        "JOB_SCAN_COMPLETED",
        worker_id=worker_id,
        run_id=run.id,
        scanned_count=len(job_batch.items),
        exhausted=job_batch.exhausted,
        cursor=job_batch.scroll_position,
        search_key=job_batch.search_key,
        next_search_key=job_batch.next_search_key,
    )


def _run_telegram_job_discovery(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    cdp_url: str,
    executor: ActionExecutor,
    rules: object,
) -> None:
    from datetime import timedelta

    policy = get_telegram_policy()
    raw_cursor = (run.cursor or {}).get("job_discovery")
    cursor = raw_cursor if isinstance(raw_cursor, dict) else {}
    raw_seen = cursor.get("seen_job_ids")
    seen_post_ids = [str(item) for item in (raw_seen if isinstance(raw_seen, list) else [])]
    retry_record = next_retryable_job(session, run)
    retryable_ids = (
        {retry_record.external_job_id} if retry_record is not None else set()
    )
    adapter = TelegramJobDiscoveryAdapter(policy)
    try:
        discovered = adapter.scan(
            cdp_url,
            seen_post_ids=[item for item in seen_post_ids if item not in retryable_ids],
        )
    except (OSError, TimeoutError, ValueError) as exc:
        runtime_event(
            logger,
            "TELEGRAM_DISCOVERY_FAILED",
            worker_id=worker_id,
            run_id=run.id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        pause_run(session, run.id, ["TELEGRAM_DISCOVERY_UNAVAILABLE"])
        return
    record_ready_platform_session(session, run, cdp_url)
    parser_config = get_job_parser_config()
    irrelevant = parser_config.irrelevant_title_keywords
    strategy = session.get(db.JobStrategy, run.strategy_id)
    relevant = (
        [
            rule.pattern
            for rule in strategy.title_rules
            if rule.rule_type == "INCLUDE"
        ]
        if strategy is not None
        else []
    )
    items: list[DiscoveredJob] = []
    for post in discovered.posts:
        if not is_potentially_relevant_title(
            post.job.title,
            [*parser_config.relevant_title_keywords, *relevant],
        ):
            reasons = ["TITLE_DIRECTION_NOT_RELEVANT"]
        elif is_obviously_irrelevant_title(
            post.job.title,
            irrelevant,
            relevant,
        ):
            reasons = ["TITLE_OBVIOUSLY_IRRELEVANT"]
        else:
            reasons = []
        items.append(
            DiscoveredJob(
                summary={
                    "external_job_id": str(post.job.external_job_id),
                    "title": post.job.title,
                    "company_name": post.job.company_name,
                },
                detail=(
                    None
                    if reasons
                    else ReadResult(
                        platform=Platform.TELEGRAM,
                        status=SessionStatus.SESSION_READY,
                        page_type=PageType.JOB,
                        page_url=(f"https://web.telegram.org/a/#{post.channel_id}"),
                        page_title=post.channel_name,
                        content_hash=hashlib.sha256(post.job.description.encode()).hexdigest(),
                        selector_version="telegram-web-a-v1",
                        job=post.job,
                    )
                ),
                reason_codes=reasons,
            )
        )
    now = datetime.now(UTC)
    batch = JobDiscoveryBatch(
        platform=Platform.TELEGRAM,
        search_key="TELEGRAM_CHANNELS",
        scroll_position=len(discovered.seen_post_ids),
        scanned_at=now,
        next_scan_at=now + timedelta(seconds=60),
        items=items,
        seen_job_ids=discovered.seen_post_ids,
        exhausted=True,
    )
    counts = process_job_discovery_batch(
        session,
        run,
        batch,
        provider=build_runtime_llm_provider(session),
        executor=executor,
        cdp_url=cdp_url,
    )
    if retry_record is not None and not any(
        item.summary.external_job_id == retry_record.external_job_id
        for item in items
    ):
        mark_retry_target_not_visible(session, retry_record, now=now)
    runtime_event(
        logger,
        "TELEGRAM_JOB_SCAN_COMPLETED",
        worker_id=worker_id,
        run_id=run.id,
        **counts,
    )


def _tick_and_log(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    executor: ActionExecutor,
) -> None:
    result = tick_run(
        session,
        run.id,
        worker_id,
        executor=executor,
    )
    runtime_event(
        logger,
        "CYCLE_COMPLETED",
        worker_id=worker_id,
        run_id=run.id,
        status=result["status"],
        processed_count=result["processed_count"],
        action_count=result["action_count"],
        failure_count=result["failure_count"],
        pause_reason_codes=result["pause_reason_codes"],
    )


def run_once(worker_id: str, cdp_url: str = "http://127.0.0.1:9222") -> None:
    with SessionLocal() as session:
        run_ids = session.scalars(
            select(db.AgentRun.id).where(db.AgentRun.status == "RUNNING")
        ).all()
    for run_id in run_ids:
        with SessionLocal() as circuit_session:
            if llm_circuit_is_open(circuit_session):
                break
        runtime_event(logger, "CYCLE_STARTED", worker_id=worker_id, run_id=run_id)
        try:
            with SessionLocal() as session:
                run = session.get(db.AgentRun, run_id)
                if run is None:
                    continue
                executor, executor_type = _build_executor(
                    run.platform, get_settings().agent_executor_mode
                )
                if run.platform == Platform.BOSS.value:
                    if not _discover_messages(
                        session,
                        run,
                        worker_id,
                        cdp_url,
                        BossMessageDiscoveryAdapter(get_browser_selectors()),
                        executor,
                    ):
                        continue
                    run.executor_type = executor_type
                    session.commit()
                    # 回复动作依赖刚打开的消息页，必须在职位扫描改变页面前完成。
                    _tick_and_log(session, run, worker_id, executor)
                    if run.status != "RUNNING":
                        continue
                    rules = _effective_rules(session, run.platform, run.strategy_id)
                    if (
                        rules.job_scan_enabled
                        and not rules.emergency_stop
                    ):
                        scan_blockers = job_scan_block_reasons(
                            session, run, rules, datetime.now(UTC)
                        )
                        if scan_blockers:
                            runtime_event(
                                logger,
                                "JOB_SCAN_SKIPPED",
                                worker_id=worker_id,
                                run_id=run_id,
                                reason_codes=scan_blockers,
                            )
                        else:
                            _run_boss_job_discovery(
                                session,
                                run,
                                worker_id,
                                cdp_url,
                                executor,
                                rules,
                            )
                    continue
                elif run.platform == Platform.MAIMAI.value:
                    if not _discover_messages(
                        session,
                        run,
                        worker_id,
                        cdp_url,
                        MaimaiMessageDiscoveryAdapter(
                            get_browser_selectors(),
                            get_recommendation_rules(),
                        ),
                        executor,
                    ):
                        continue
                    if not _process_maimai_recommendations(
                        session, run, worker_id, cdp_url, executor
                    ):
                        continue
                elif run.platform == Platform.TELEGRAM.value:
                    rules = _effective_rules(session, run.platform, run.strategy_id)
                    scan_blockers = job_scan_block_reasons(session, run, rules, datetime.now(UTC))
                    if scan_blockers:
                        runtime_event(
                            logger,
                            "TELEGRAM_JOB_SCAN_SKIPPED",
                            worker_id=worker_id,
                            run_id=run_id,
                            reason_codes=scan_blockers,
                        )
                    else:
                        _run_telegram_job_discovery(
                            session,
                            run,
                            worker_id,
                            cdp_url,
                            executor,
                            rules,
                        )
                run.executor_type = executor_type
                session.commit()
                _tick_and_log(session, run, worker_id, executor)
        except ValueError as exc:
            runtime_event(
                logger,
                "CYCLE_SKIPPED",
                worker_id=worker_id,
                run_id=run_id,
                reason=type(exc).__name__,
            )
        except Exception:
            runtime_event(
                logger,
                "CYCLE_FAILED",
                worker_id=worker_id,
                run_id=run_id,
                reason="UNEXPECTED_ERROR",
            )
            logger.exception("Agent tick failed unexpectedly: run=%s", run_id)


def maintenance_once(cdp_url: str) -> None:
    with SessionLocal() as session:
        process_reconciliation_queue(session, cdp_url)
        apply_retention(session)


def _request_stop(_signum: int, _frame: object) -> None:
    STOP_EVENT.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    with SessionLocal() as config_session:
        selected_settings = runtime_settings(config_session, settings)
    log_path = configure_runtime_logging(settings)
    install_redacting_filter()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    runtime_event(
        logger,
        "WORKER_STARTING",
        worker_id=worker_id,
        log_path=log_path,
        llm_provider=selected_settings.llm_provider,
        llm_model=selected_settings.llm_model,
        executor_mode=settings.agent_executor_mode,
    )
    cdp_url = os.getenv("AGENT_CDP_URL", "http://127.0.0.1:9222")
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    with _single_worker_lock():
        with SessionLocal() as session:
            failures = worker_preflight(session, settings, cdp_url)
            if failures:
                raise RuntimeError(f"Worker 启动自检失败：{','.join(failures)}")
            register_worker(
                session,
                worker_id,
                socket.gethostname(),
                os.getpid(),
                metadata={
                    "executor_mode": settings.agent_executor_mode,
                    "selector_version": get_browser_selectors().version,
                },
            )
        heartbeat_stop_event = threading.Event()
        heartbeat_interval = max(5, min(settings.worker_stale_seconds // 3, 30))
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(worker_id, heartbeat_interval, heartbeat_stop_event),
            name="agent-worker-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            while not STOP_EVENT.is_set():
                try:
                    with SessionLocal() as circuit_session:
                        circuit_open = llm_circuit_is_open(circuit_session)
                        if circuit_open:
                            settings = reload_settings()
                            selected_settings = runtime_settings(
                                circuit_session, settings
                            )
                            circuit = probe_llm_circuit(
                                circuit_session,
                                selected_settings,
                                build_runtime_llm_provider(
                                    circuit_session, selected_settings
                                ),
                            )
                            runtime_event(
                                logger,
                                "LLM_CIRCUIT_WAITING",
                                worker_id=worker_id,
                                **circuit,
                            )
                    if not circuit_open:
                        run_once(worker_id, cdp_url)
                        maintenance_once(cdp_url)
                except Exception:
                    logger.exception("Worker loop failed; will retry safely")
                STOP_EVENT.wait(settings.agent_poll_interval_seconds)
        finally:
            heartbeat_stop_event.set()
            heartbeat_thread.join(timeout=heartbeat_interval + 1)
            with SessionLocal() as session:
                stop_worker(session, worker_id)


if __name__ == "__main__":
    main()
