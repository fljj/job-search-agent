import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from adapters.llm.errors import (
    LlmAuthenticationError,
    LlmInvalidResponseError,
    LlmNetworkError,
    LlmRateLimitError,
    LlmServiceError,
    LlmTimeoutError,
)


class JsonTransport(Protocol):
    def post(
        self, url: str, headers: dict[str, str], payload: dict[str, object], timeout: int
    ) -> dict[str, object]: ...


class UrllibJsonTransport:
    def post(
        self, url: str, headers: dict[str, str], payload: dict[str, object], timeout: int
    ) -> dict[str, object]:
        request = Request(
            url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise LlmAuthenticationError("模型认证失败") from None
            if exc.code == 429:
                raise LlmRateLimitError("模型请求被限流") from None
            raise LlmServiceError(f"模型服务返回 HTTP {exc.code}") from None
        except TimeoutError:
            raise LlmTimeoutError("模型请求超时") from None
        except URLError:
            raise LlmNetworkError("无法连接模型服务") from None
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise LlmInvalidResponseError("模型服务响应不是 JSON") from None
        if not isinstance(result, dict):
            raise LlmInvalidResponseError("模型服务响应结构无效")
        return result
