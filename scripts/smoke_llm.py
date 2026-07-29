"""手动真实 LLM 冒烟；仅在显式执行且配置 API Key 时发起一次请求。"""

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.llm_config_service import build_runtime_llm_provider
from packages.llm.models import MessageClassificationRequest


def main() -> None:
    with SessionLocal() as session:
        provider = build_runtime_llm_provider(session)
    result = provider.classify_message(MessageClassificationRequest(message="方便发一份简历吗？"))
    print(result.data.model_dump_json())  # noqa: T201
    print(result.metadata.model_dump_json())  # noqa: T201


if __name__ == "__main__":
    main()
