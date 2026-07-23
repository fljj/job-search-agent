from types import SimpleNamespace

from adapters.llm.fake import FakeLlmProvider
from apps.api.app.services.score_service import _fingerprint


class OtherModelFakeProvider(FakeLlmProvider):
    @property
    def model_name(self) -> str:
        return "other-model"


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
