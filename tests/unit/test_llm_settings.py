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


def test_provider_catalog_uses_provider_specific_models_and_keys() -> None:
    settings = Settings(
        _env_file=None,
        llm_providers="QWEN,ZHIPU",
        qwen_model="qwen-plus-test",
        zhipu_model="glm-test",
        qwen_api_key=SecretStr("qwen-key"),
        zhipu_api_key=SecretStr("zhipu-key"),
    )

    assert settings.available_llm_providers == ["QWEN", "ZHIPU"]
    qwen = settings.with_llm_selection("QWEN", settings.qwen_model)
    zhipu = settings.with_llm_selection("ZHIPU", settings.zhipu_model)
    assert qwen.llm_model == "qwen-plus-test"
    assert qwen.selected_llm_api_key is settings.qwen_api_key
    assert zhipu.llm_model == "glm-test"
    assert zhipu.selected_llm_api_key is settings.zhipu_api_key


def test_non_local_api_binding_requires_access_token() -> None:
    with pytest.raises(ValueError, match="API_ACCESS_TOKEN"):
        Settings(_env_file=None, api_host="0.0.0.0", api_access_token=None)

    settings = Settings(
        _env_file=None,
        api_host="0.0.0.0",
        api_access_token=SecretStr("local-test-token"),
    )
    assert settings.api_host == "0.0.0.0"


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
