import logging
import re

_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)(api[_-]?key[=:]\s*)[^\s,;]+"),
    re.compile(r"(postgresql(?:\+\w+)?://[^:\s]+:)[^@\s]+(@)"),
)


def redact_text(value: str) -> str:
    result = value
    for pattern in _PATTERNS:
        replacement = r"\1***\2" if pattern.groups == 2 else r"\1***"
        result = pattern.sub(replacement, result)
    return result


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def install_redacting_filter() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, RedactingFilter) for item in handler.filters):
            handler.addFilter(RedactingFilter())
