import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apps.api.app.core.config import Settings
from packages.audit.redaction import RedactingFilter


def configure_gray_logging(settings: Settings) -> Path:
    """配置灰度运行日志；文件轮转且所有输出经过凭证脱敏。"""
    log_dir = Path(settings.agent_log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "agent-gray.log"
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename) == log_path
        for handler in root.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.agent_log_max_bytes,
            backupCount=settings.agent_log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    return log_path


def gray_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """输出可机器检索的灰度事件，不接受消息正文、Cookie 或密钥字段。"""
    logger.info(
        json.dumps(
            {"event": event, **fields},
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )
    )
