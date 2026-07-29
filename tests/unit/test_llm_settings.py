import pytest
from pydantic import SecretStr

from apps.api.app.core.config import Settings, get_settings, reload_settings


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


def test_reload_settings_reads_updated_llm_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", "first-test-key")
    first = reload_settings()
    assert first.selected_llm_api_key is not None
    assert first.selected_llm_api_key.get_secret_value() == "first-test-key"

    monkeypatch.setenv("ZHIPU_API_KEY", "second-test-key")
    cached = get_settings()
    assert cached.selected_llm_api_key is not None
    assert cached.selected_llm_api_key.get_secret_value() == "first-test-key"

    refreshed = reload_settings()
    assert refreshed.selected_llm_api_key is not None
    assert refreshed.selected_llm_api_key.get_secret_value() == "second-test-key"
