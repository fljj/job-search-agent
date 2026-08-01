from adapters.llm.http import JsonTransport
from adapters.llm.qwen import QwenLlmProvider


class ZhipuLlmProvider(QwenLlmProvider):
    """智谱 OpenAI 兼容接口；领域契约与供应商实现保持隔离。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 30,
        max_retries: int = 1,
        transport: JsonTransport | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            provider_name="ZHIPU",
            transport=transport,
            request_options={
                "thinking": {"type": "disabled"},
                "reasoning_effort": "none",
                "do_sample": False,
            },
        )
