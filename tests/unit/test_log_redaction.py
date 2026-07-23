import logging

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
