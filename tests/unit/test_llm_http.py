import io
from urllib.error import HTTPError, URLError

import pytest

from adapters.llm.errors import (
    LlmAuthenticationError,
    LlmNetworkError,
    LlmRateLimitError,
    LlmServiceError,
    LlmTimeoutError,
)
from adapters.llm.http import UrllibJsonTransport


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LlmAuthenticationError),
        (403, LlmAuthenticationError),
        (429, LlmRateLimitError),
        (500, LlmServiceError),
    ],
)
def test_http_status_is_classified(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: type[Exception]
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise HTTPError("https://example.invalid", status, "failed", {}, io.BytesIO())

    monkeypatch.setattr("adapters.llm.http.urlopen", fail)
    with pytest.raises(expected):
        UrllibJsonTransport().post("https://example.invalid", {}, {}, 1)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), LlmTimeoutError),
        (URLError("offline"), LlmNetworkError),
    ],
)
def test_transport_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("adapters.llm.http.urlopen", fail)
    with pytest.raises(expected):
        UrllibJsonTransport().post("https://example.invalid", {}, {}, 1)
