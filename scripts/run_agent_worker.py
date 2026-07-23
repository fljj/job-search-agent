"""单实例短轮询 Worker；真实平台使用本机 CDP，MOCK 保持离线执行。"""

import fcntl
import logging
import os
import signal
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.job_discovery import BossJobDiscoveryAdapter
from adapters.browser.message_discovery import BossMessageDiscoveryAdapter
from adapters.browser.playwright_actions import PlaywrightActionExecutor
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.config import get_settings
from apps.api.app.core.database import SessionLocal
from apps.api.app.core.llm import build_llm_provider
from apps.api.app.models import entities as db
from apps.api.app.services.agent_service import pause_run, tick_run
from apps.api.app.services.automation_service import _effective_rules
from apps.api.app.services.job_discovery_service import process_job_discovery_batch
from apps.api.app.services.message_discovery_service import persist_discovery_batch
from apps.api.app.services.operations_service import (
    apply_retention,
    heartbeat_worker,
    process_reconciliation_queue,
    register_worker,
    stop_worker,
    worker_preflight,
)
from packages.audit.redaction import install_redacting_filter
from packages.browser_worker.actions import ActionExecutor
from packages.browser_worker.models import Platform

logger = logging.getLogger(__name__)
LOCK_PATH = "/tmp/job-search-agent-worker.lock"
STOP_EVENT = threading.Event()


def _build_executor(platform: str, mode: str) -> tuple[ActionExecutor, str]:
    if platform == Platform.BOSS.value:
        if mode != "REAL":
            raise ValueError("BOSS 正式运行禁止使用 Fake 执行器")
        return PlaywrightActionExecutor(get_browser_selectors()), "REAL_CDP"
    if platform == "MOCK":
        if mode != "FAKE":
            raise ValueError("MOCK 运行必须显式配置 Fake 执行器")
        return FakeActionExecutor(), "FAKE"
    raise ValueError(f"平台 {platform} 尚无正式执行器")


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


def run_once(worker_id: str, cdp_url: str = "http://127.0.0.1:9222") -> None:
    with SessionLocal() as session:
        run_ids = session.scalars(
            select(db.AgentRun.id).where(db.AgentRun.status == "RUNNING")
        ).all()
    for run_id in run_ids:
        try:
            with SessionLocal() as session:
                run = session.get(db.AgentRun, run_id)
                if run is None:
                    continue
                executor, executor_type = _build_executor(
                    run.platform, get_settings().agent_executor_mode
                )
                if run.platform == Platform.BOSS.value:
                    root_cursor = run.cursor or {}
                    raw_message_cursor = root_cursor.get("message_discovery")
                    cursor = (
                        raw_message_cursor
                        if isinstance(raw_message_cursor, dict)
                        else {}
                    )
                    raw_position = cursor.get("scroll_position")
                    raw_seen = cursor.get("seen_message_keys")
                    try:
                        batch = BossMessageDiscoveryAdapter(
                            get_browser_selectors()
                        ).scan(
                            cdp_url,
                            partition=str(cursor.get("partition") or "UNREAD"),
                            scroll_position=(
                                raw_position if isinstance(raw_position, int) else 0
                            ),
                            seen_message_keys=[
                                str(item)
                                for item in (
                                    raw_seen if isinstance(raw_seen, list) else []
                                )
                            ],
                            limit=get_settings().agent_tick_batch_size,
                        )
                    except ValueError:
                        pause_run(
                            session, run_id, ["MESSAGE_DISCOVERY_UNAVAILABLE"]
                        )
                        continue
                    persist_discovery_batch(session, run, worker_id, batch)
                    rules = _effective_rules(
                        session, run.platform, run.strategy_id
                    )
                    if rules.job_scan_enabled and not rules.emergency_stop:
                        raw_job_cursor = (run.cursor or {}).get("job_discovery")
                        job_cursor = (
                            raw_job_cursor
                            if isinstance(raw_job_cursor, dict)
                            else {}
                        )
                        raw_job_position = job_cursor.get("scroll_position")
                        raw_seen_jobs = job_cursor.get("seen_job_ids")
                        try:
                            job_batch = BossJobDiscoveryAdapter(
                                get_browser_selectors()
                            ).scan(
                                cdp_url,
                                search_key=str(
                                    job_cursor.get("search_key") or "CURRENT_SEARCH"
                                ),
                                scroll_position=(
                                    raw_job_position
                                    if isinstance(raw_job_position, int)
                                    else 0
                                ),
                                seen_job_ids=[
                                    str(item)
                                    for item in (
                                        raw_seen_jobs
                                        if isinstance(raw_seen_jobs, list)
                                        else []
                                    )
                                ],
                                limit=min(
                                    get_settings().agent_tick_batch_size,
                                    rules.hourly_scan_limit,
                                ),
                            )
                        except ValueError:
                            pause_run(
                                session, run_id, ["JOB_DISCOVERY_UNAVAILABLE"]
                            )
                            continue
                        process_job_discovery_batch(
                            session,
                            run,
                            job_batch,
                            provider=build_llm_provider(get_settings()),
                            executor=executor,
                            cdp_url=cdp_url,
                        )
                run.executor_type = executor_type
                session.commit()
                tick_run(
                    session,
                    run_id,
                    worker_id,
                    executor=executor,
                )
        except ValueError as exc:
            logger.info("Agent tick skipped: run=%s reason=%s", run_id, exc)
        except Exception:
            logger.exception("Agent tick failed unexpectedly: run=%s", run_id)


def maintenance_once(cdp_url: str) -> None:
    with SessionLocal() as session:
        process_reconciliation_queue(session, cdp_url)
        apply_retention(session)


def _request_stop(_signum: int, _frame: object) -> None:
    STOP_EVENT.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    install_redacting_filter()
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
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
        try:
            while not STOP_EVENT.is_set():
                try:
                    run_once(worker_id, cdp_url)
                    maintenance_once(cdp_url)
                    with SessionLocal() as session:
                        heartbeat_worker(session, worker_id)
                except Exception:
                    logger.exception("Worker loop failed; will retry safely")
                STOP_EVENT.wait(settings.agent_poll_interval_seconds)
        finally:
            with SessionLocal() as session:
                stop_worker(session, worker_id)


if __name__ == "__main__":
    main()
