from types import SimpleNamespace

from adapters.llm.fake import FakeLlmProvider
from apps.api.app.services.score_service import _fingerprint


class OtherModelFakeProvider(FakeLlmProvider):
    @property
    def model_name(self) -> str:
        return "other-model"


class OtherPromptFakeProvider(FakeLlmProvider):
    def prompt_version(self, purpose: str) -> str:
        return f"{super().prompt_version(purpose)}-changed"


def test_score_fingerprint_includes_llm_identity() -> None:
    strategy = SimpleNamespace(id="strategy", version=1)
    profile = SimpleNamespace(id="profile", version=1)
    parsed = SimpleNamespace(id="parsed")

    first = _fingerprint(
        "job", strategy, profile, parsed, FakeLlmProvider()  # type: ignore[arg-type]
    )
    second = _fingerprint(
        "job",
        strategy,  # type: ignore[arg-type]
        profile,  # type: ignore[arg-type]
        parsed,  # type: ignore[arg-type]
        OtherModelFakeProvider(),
    )

    assert first != second


def test_score_fingerprint_changes_when_prompt_contract_changes() -> None:
    strategy = SimpleNamespace(id="strategy", version=1)
    profile = SimpleNamespace(id="profile", version=1)
    parsed = SimpleNamespace(id="parsed")

    first = _fingerprint(
        "job", strategy, profile, parsed, FakeLlmProvider()  # type: ignore[arg-type]
    )
    second = _fingerprint(
        "job",
        strategy,  # type: ignore[arg-type]
        profile,  # type: ignore[arg-type]
        parsed,  # type: ignore[arg-type]
        OtherPromptFakeProvider(),
    )

    assert first != second
