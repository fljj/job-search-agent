class LlmProviderError(Exception):
    code = "LLM_PROVIDER_ERROR"

    def __init__(self, message: str, *, attempt_number: int = 1) -> None:
        super().__init__(message)
        self.attempt_number = attempt_number


class LlmConfigurationError(LlmProviderError):
    code = "LLM_NOT_CONFIGURED"


class LlmAuthenticationError(LlmProviderError):
    code = "LLM_AUTHENTICATION_FAILED"


class LlmRateLimitError(LlmProviderError):
    code = "LLM_RATE_LIMITED"


class LlmTimeoutError(LlmProviderError):
    code = "LLM_TIMEOUT"


class LlmNetworkError(LlmProviderError):
    code = "LLM_NETWORK_ERROR"


class LlmServiceError(LlmProviderError):
    code = "LLM_SERVICE_ERROR"


class LlmInvalidResponseError(LlmProviderError):
    code = "LLM_INVALID_RESPONSE"
