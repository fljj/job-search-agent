"""手动千问冒烟；仅在显式执行且配置 API Key 时发起一次请求。"""

from apps.api.app.core.config import get_settings
from apps.api.app.core.llm import build_llm_provider
from packages.llm.models import MessageClassificationRequest


def main() -> None:
    provider = build_llm_provider(get_settings())
    result = provider.classify_message(MessageClassificationRequest(message="方便发一份简历吗？"))
    print(result.data.model_dump_json())  # noqa: T201
    print(result.metadata.model_dump_json())  # noqa: T201


if __name__ == "__main__":
    main()
