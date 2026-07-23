"""阶段五单实例短轮询 Worker；动作执行固定使用 FakeActionExecutor。"""

import logging
import os
import socket
import time

from sqlalchemy import select

from adapters.browser.fake_actions import FakeActionExecutor
from apps.api.app.core.config import get_settings
from apps.api.app.core.database import SessionLocal
from apps.api.app.models import entities as db
from apps.api.app.services.agent_service import tick_run

logger = logging.getLogger(__name__)


def run_once(worker_id: str) -> None:
    with SessionLocal() as session:
        run_ids = session.scalars(
            select(db.AgentRun.id).where(db.AgentRun.status == "RUNNING")
        ).all()
    for run_id in run_ids:
        try:
            with SessionLocal() as session:
                tick_run(
                    session,
                    run_id,
                    worker_id,
                    executor=FakeActionExecutor(),
                )
        except ValueError as exc:
            logger.info("Agent tick skipped: run=%s reason=%s", run_id, exc)
        except Exception:
            logger.exception("Agent tick failed unexpectedly: run=%s", run_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        run_once(worker_id)
        time.sleep(settings.agent_poll_interval_seconds)


if __name__ == "__main__":
    main()
