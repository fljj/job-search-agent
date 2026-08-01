from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from apps.api.app.core.config import Settings
from apps.api.app.models import entities as db
from apps.api.app.services.llm_config_service import select_llm_configuration


def test_runtime_selection_accepts_free_form_model_for_enabled_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_providers="ZHIPU,QWEN",
        qwen_model="qwen-plus",
        qwen_api_key=SecretStr("configured"),
    )
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.get_settings", lambda: settings
    )
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.ensure_default_user", lambda _: None
    )
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.llm_configuration",
        lambda _: {
            "provider": "QWEN",
            "model": "qwen-max-latest",
            "timeout_seconds": 180,
            "configured": True,
            "options": [],
        },
    )
    session = MagicMock(spec=Session)
    session.scalar.side_effect = [None, None]

    result = select_llm_configuration(session, "qwen", " qwen-max-latest ", 180)

    saved = session.add.call_args.args[0]
    assert isinstance(saved, db.LlmRuntimeSetting)
    assert saved.provider == "QWEN"
    assert saved.model == "qwen-max-latest"
    assert saved.timeout_seconds == 180
    assert result["model"] == "qwen-max-latest"
    session.commit.assert_called_once()


def test_runtime_selection_rejects_provider_not_enabled_in_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.get_settings",
        lambda: Settings(_env_file=None, llm_providers="ZHIPU"),
    )

    with pytest.raises(ValueError, match="未在环境配置中启用"):
        select_llm_configuration(MagicMock(spec=Session), "QWEN", "qwen-max", 120)


def test_runtime_selection_rejects_blank_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.get_settings",
        lambda: Settings(
            _env_file=None,
            llm_providers="ZHIPU",
            zhipu_api_key=SecretStr("configured"),
        ),
    )

    with pytest.raises(ValueError, match="模型名称不能为空"):
        select_llm_configuration(MagicMock(spec=Session), "ZHIPU", "   ", 120)


def test_runtime_selection_rejects_timeout_outside_supported_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.get_settings",
        lambda: Settings(
            _env_file=None,
            llm_providers="ZHIPU",
            zhipu_api_key=SecretStr("configured"),
        ),
    )

    with pytest.raises(ValueError, match="1 到 300 秒"):
        select_llm_configuration(MagicMock(spec=Session), "ZHIPU", "glm-test", 301)
