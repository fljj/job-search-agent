import pytest
from pydantic import SecretStr

from apps.api.app.api.v1.system import llm_status
from apps.api.app.core.config import Settings


def test_llm_status_never_exposes_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="QWEN",
        llm_model="qwen-test",
        llm_api_key=SecretStr("must-not-leak"),
    )
    monkeypatch.setattr("apps.api.app.api.v1.system.get_settings", lambda: settings)

    result = llm_status()

    assert result["data"] == {
        "provider": "QWEN",
        "model": "qwen-test",
        "configured": True,
    }
    assert "must-not-leak" not in str(result)
