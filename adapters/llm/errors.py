class LlmProviderError(Exception):
    code = "LLM_PROVIDER_ERROR"


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
