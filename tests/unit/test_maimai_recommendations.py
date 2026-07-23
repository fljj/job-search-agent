import pytest

from adapters.browser.maimai_recommendations import (
    MaimaiRecommendationCard,
    _control_expression,
    _external_id,
)


def test_external_recommendation_id_comes_from_stable_mid() -> None:
    assert _external_id('{"mid": 12345, "other": "ignored"}') == "12345"


@pytest.mark.parametrize("value", ["{}", "not-json"])
def test_missing_external_id_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="稳定 ID"):
        _external_id(value)


def test_card_hash_is_stable_and_content_sensitive() -> None:
    first = MaimaiRecommendationCard(
        external_recommendation_id="1",
        recruiter_name="招聘人",
        recruiter_title="公司·招聘",
        company_name="公司",
        job_title="Java 开发",
        card_text="推荐内容",
    )
    second = first.model_copy(update={"card_text": "另一条推荐内容"})
    assert first.card_hash == first.card_hash
    assert first.card_hash != second.card_hash


def test_control_expression_requires_exact_visible_single_control() -> None:
    expression = _control_expression("同意", click=True)
    assert "matches.length !== 1" not in expression
    assert "matches[0].click()" in expression
    assert "getClientRects().length > 0" in expression
