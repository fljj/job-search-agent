import logging
from pathlib import Path

from apps.api.app.core.config import Settings
from packages.audit.gray_logging import configure_gray_logging, gray_event
from packages.audit.redaction import RedactingFilter, redact_text


def test_redacts_tokens_api_keys_and_database_passwords() -> None:
    value = (
        "Authorization: Bearer secret-token API_KEY=secret "
        "postgresql+psycopg://user:password@localhost/db"
    )
    redacted = redact_text(value)
    assert "secret-token" not in redacted
    assert "API_KEY=secret" not in redacted
    assert ":password@" not in redacted
    assert redacted.count("***") == 3


def test_logging_filter_clears_unsafe_arguments() -> None:
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "API_KEY=%s", ("secret",), None
    )
    assert RedactingFilter().filter(record)
    assert record.getMessage() == "API_KEY=***"


def test_gray_log_is_rotating_structured_and_redacted(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        agent_log_dir=str(tmp_path),
        agent_log_max_bytes=1_000_000,
        agent_log_backup_count=2,
    )
    log_path = configure_gray_logging(settings)
    logger = logging.getLogger("gray-test")
    gray_event(logger, "TEST_EVENT", run_id="run-1", detail="API_KEY=secret")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text(encoding="utf-8")
    assert '"event": "TEST_EVENT"' in content
    assert '"run_id": "run-1"' in content
    assert "secret" not in content
