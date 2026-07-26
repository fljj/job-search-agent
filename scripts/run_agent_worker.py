"""单实例短轮询 Worker；真实平台使用本机 CDP，MOCK 保持离线执行。"""

import fcntl
import logging
import os
import signal
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.job_discovery import BossJobDiscoveryAdapter
from adapters.browser.message_discovery import (
    BossMessageDiscoveryAdapter,
    MaimaiMessageDiscoveryAdapter,
    MessageDiscoveryAdapter,
)
from adapters.browser.playwright_actions import PlaywrightActionExecutor
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.config import get_settings
from apps.api.app.core.database import SessionLocal
from apps.api.app.core.llm import build_llm_provider
from apps.api.app.core.recommendation_config import get_recommendation_rules
from apps.api.app.models import entities as db
from apps.api.app.services.agent_service import pause_run, tick_run
from apps.api.app.services.automation_service import _effective_rules
from apps.api.app.services.job_discovery_service import (
    job_scan_block_reasons,
    process_job_discovery_batch,
)
from apps.api.app.services.message_discovery_service import (
    persist_discovery_batch,
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
from apps.api.app.services.rollout_service import (
    allows_rollout_job_scan,
    enforce_rollout_health,
)
from packages.audit.gray_logging import configure_gray_logging, gray_event
from packages.audit.redaction import install_redacting_filter
from packages.browser_worker.actions import ActionExecutor
from packages.browser_worker.models import Platform

logger = logging.getLogger(__name__)
LOCK_PATH = "/tmp/job-search-agent-worker.lock"
STOP_EVENT = threading.Event()


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
    if platform in {Platform.BOSS.value, Platform.MAIMAI.value}:
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
) -> bool:
    raw_cursor = (run.cursor or {}).get("message_discovery")
    cursor = raw_cursor if isinstance(raw_cursor, dict) else {}
    raw_position = cursor.get("scroll_position")
    raw_seen = cursor.get("seen_message_keys")
    try:
        batch = adapter.scan(
            cdp_url,
        partition="ALL",
            scroll_position=raw_position if isinstance(raw_position, int) else 0,
            seen_message_keys=[
                str(item)
                for item in (raw_seen if isinstance(raw_seen, list) else [])
            ],
            limit=get_settings().agent_tick_batch_size,
        )
    except (OSError, TimeoutError, ValueError):
        pause_run(session, run.id, ["MESSAGE_DISCOVERY_UNAVAILABLE"])
        return False
    record_ready_platform_session(session, run, cdp_url)
    counts = persist_discovery_batch(session, run, worker_id, batch)
    gray_event(
        logger,
        "MESSAGE_SCAN_COMPLETED",
        worker_id=worker_id,
        run_id=run.id,
        platform=run.platform,
        scanned_count=len(batch.items),
        imported_count=counts["imported"],
        paused_count=counts["paused"],
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
    gray_event(
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
    job_cursor = (
        raw_job_cursor if isinstance(raw_job_cursor, dict) else {}
    )
    raw_job_position = job_cursor.get("scroll_position")
    raw_seen_jobs = job_cursor.get("seen_job_ids")
    search_keys = get_settings().boss_job_searches
    try:
        job_batch = BossJobDiscoveryAdapter(get_browser_selectors()).scan(
            cdp_url,
            search_key=str(
                job_cursor.get("search_key")
                or (search_keys[0] if search_keys else "CURRENT_SEARCH")
            ),
            search_keys=search_keys,
            refresh_before_scan=bool(
                job_cursor.get("refresh_before_scan", False)
            ),
            switch_search_before_scan=bool(
                job_cursor.get(
                    "switch_search_before_scan",
                    not bool(raw_job_cursor),
                )
            ),
            scroll_position=(
                raw_job_position if isinstance(raw_job_position, int) else 0
            ),
            previous_cursor=(
                str(job_cursor["next_cursor"])
                if job_cursor.get("next_cursor")
                else None
            ),
            seen_job_ids=[
                str(item)
                for item in (
                    raw_seen_jobs if isinstance(raw_seen_jobs, list) else []
                )
            ],
            limit=min(
                get_settings().agent_tick_batch_size,
                rules.hourly_scan_limit,
            ),
        )
    except (OSError, TimeoutError, ValueError):
        pause_run(session, run.id, ["JOB_DISCOVERY_UNAVAILABLE"])
        return
    process_job_discovery_batch(
        session,
        run,
        job_batch,
        provider=build_llm_provider(get_settings()),
        executor=executor,
        cdp_url=cdp_url,
    )
    gray_event(
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


def run_once(worker_id: str, cdp_url: str = "http://127.0.0.1:9222") -> None:
    with SessionLocal() as session:
        run_ids = session.scalars(
            select(db.AgentRun.id).where(db.AgentRun.status == "RUNNING")
        ).all()
    for run_id in run_ids:
        gray_event(logger, "CYCLE_STARTED", worker_id=worker_id, run_id=run_id)
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
                    ):
                        continue
                    rules = _effective_rules(
                        session, run.platform, run.strategy_id
                    )
                    if (
                        rules.job_scan_enabled
                        and not rules.emergency_stop
                        and allows_rollout_job_scan(session, run.platform)
                    ):
                        scan_blockers = job_scan_block_reasons(
                            session, run, rules, datetime.now(UTC)
                        )
                        if scan_blockers:
                            gray_event(
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
                    ):
                        continue
                    if not _process_maimai_recommendations(
                        session, run, worker_id, cdp_url, executor
                    ):
                        continue
                run.executor_type = executor_type
                session.commit()
                result = tick_run(
                    session,
                    run_id,
                    worker_id,
                    executor=executor,
                )
                gray_event(
                    logger,
                    "CYCLE_COMPLETED",
                    worker_id=worker_id,
                    run_id=run_id,
                    status=result["status"],
                    processed_count=result["processed_count"],
                    action_count=result["action_count"],
                    failure_count=result["failure_count"],
                    pause_reason_codes=result["pause_reason_codes"],
                )
        except ValueError as exc:
            gray_event(
                logger,
                "CYCLE_SKIPPED",
                worker_id=worker_id,
                run_id=run_id,
                reason=type(exc).__name__,
            )
        except Exception:
            gray_event(
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
        enforce_rollout_health(session, "BOSS")
        enforce_rollout_health(session, "MAIMAI")
        apply_retention(session)


def _request_stop(_signum: int, _frame: object) -> None:
    STOP_EVENT.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    log_path = configure_gray_logging(settings)
    install_redacting_filter()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    gray_event(
        logger,
        "WORKER_STARTING",
        worker_id=worker_id,
        log_path=log_path,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
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
