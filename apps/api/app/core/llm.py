from adapters.llm.errors import LlmConfigurationError
from adapters.llm.fake import FakeLlmProvider
from adapters.llm.qwen import QwenLlmProvider
from adapters.llm.zhipu import ZhipuLlmProvider
from apps.api.app.core.config import Settings
from packages.llm.ports import LlmProvider


def build_llm_provider(settings: Settings) -> LlmProvider:
    provider = settings.llm_provider.upper()
    if provider == "FAKE":
        return FakeLlmProvider()
    if provider not in {"QWEN", "ZHIPU"}:
        raise LlmConfigurationError(f"不支持的 LLM_PROVIDER: {provider}")
    api_key = settings.selected_llm_api_key
    if not settings.llm_configured or api_key is None:
        raise LlmConfigurationError("模型未配置")
    provider_type = ZhipuLlmProvider if provider == "ZHIPU" else QwenLlmProvider
    return provider_type(
        api_key=api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
