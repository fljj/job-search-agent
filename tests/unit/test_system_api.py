import pytest

from apps.api.app.api.v1.system import llm_status


def test_llm_status_never_exposes_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.api.app.api.v1.system.llm_configuration",
        lambda _: {
            "provider": "QWEN",
            "model": "qwen-test",
            "configured": True,
            "options": [
                {"provider": "QWEN", "model": "qwen-test", "configured": True}
            ],
        },
    )

    result = llm_status(object())  # type: ignore[arg-type]

    assert result["data"] == {
        "provider": "QWEN",
        "model": "qwen-test",
        "configured": True,
        "options": [
            {"provider": "QWEN", "model": "qwen-test", "configured": True}
        ],
    }
    assert "api_key" not in str(result).lower()
