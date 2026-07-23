"""单实例短轮询 Worker；真实平台使用本机 CDP，MOCK 保持离线执行。"""

import fcntl
import logging
import os
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.playwright_actions import PlaywrightActionExecutor
from adapters.browser.playwright_reader import BossReadOnlyAdapter
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.config import get_settings
from apps.api.app.core.database import SessionLocal
from apps.api.app.models import entities as db
from apps.api.app.schemas.browser import BrowserReadRequest
from apps.api.app.services.agent_service import tick_run
from apps.api.app.services.browser_service import persist_read_result
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.actions import ActionExecutor
from packages.browser_worker.models import PageType, Platform, SessionStatus

logger = logging.getLogger(__name__)
LOCK_PATH = "/tmp/job-search-agent-worker.lock"


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


def _sync_current_boss_conversation(cdp_url: str) -> None:
    config = get_browser_selectors()
    result = BossReadOnlyAdapter(config).read_current_page(cdp_url)
    if (
        result.status is not SessionStatus.SESSION_READY
        or result.page_type is not PageType.CONVERSATION
        or result.conversation is None
    ):
        return
    with SessionLocal() as session:
        conversation = session.scalar(
            select(db.Conversation).where(
                db.Conversation.user_id == DEFAULT_USER_ID,
                db.Conversation.platform == Platform.BOSS.value,
                db.Conversation.external_conversation_id
                == result.conversation.external_conversation_id,
            )
        )
        if conversation is None:
            logger.info(
                "Current BOSS conversation is not bound to a scored job: %s",
                result.conversation.external_conversation_id,
            )
            return
        persist_read_result(
            session,
            BrowserReadRequest(
                platform=Platform.BOSS,
                cdp_url=cdp_url,
                job_id=conversation.job_id,
                expected_recruiter=conversation.recruiter_name,
            ),
            result,
        )


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
                    _sync_current_boss_conversation(cdp_url)
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


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    cdp_url = os.getenv("AGENT_CDP_URL", "http://127.0.0.1:9222")
    with _single_worker_lock():
        while True:
            run_once(worker_id, cdp_url)
            time.sleep(settings.agent_poll_interval_seconds)


if __name__ == "__main__":
    main()
