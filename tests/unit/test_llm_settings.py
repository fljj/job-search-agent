from pydantic import SecretStr

from apps.api.app.core.config import Settings


def test_llm_is_unconfigured_without_api_key() -> None:
    settings = Settings(_env_file=None, llm_api_key=None)
    assert settings.llm_provider == "ZHIPU"
    assert settings.llm_model == "glm-5.2"
    assert settings.llm_configured is False


def test_llm_api_key_is_secret_and_not_exposed_by_repr() -> None:
    settings = Settings(_env_file=None, zhipu_api_key=SecretStr("test-secret"))
    assert settings.llm_configured is True
    assert "test-secret" not in repr(settings)


def test_zhipu_never_reuses_qwen_api_key() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="ZHIPU",
        llm_api_key=SecretStr("qwen-secret"),
        zhipu_api_key=None,
    )

    assert settings.llm_configured is False
