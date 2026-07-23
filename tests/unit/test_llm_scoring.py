from decimal import Decimal

import pytest

from packages.llm.models import JobScoreOutput, ScoreDimension
from packages.scoring.llm_engine import (
    EVIDENCE_REFS,
    LlmScoreValidationError,
    is_automation_eligible,
    validate_llm_score,
)
from packages.scoring.models import DIMENSION_MAX, Eligibility, Grade, ScoringContext


def output_for_total(total: int, *, recommends: bool = True) -> JobScoreOutput:
    remaining = Decimal(total)
    dimensions: list[ScoreDimension] = []
    for dimension, maximum in DIMENSION_MAX.items():
        score = min(remaining, maximum)
        remaining -= score
        dimensions.append(
            ScoreDimension(
                dimension=dimension,
                score=score,
                max_score=maximum,
                reason=f"{dimension} test",
                evidence_refs=[sorted(EVIDENCE_REFS[dimension])[0]],
            )
        )
    assert remaining == 0
    return JobScoreOutput(
        dimensions=dimensions,
        total_score=total,
        match_reasons=["匹配理由"],
        risk_notes=["风险提示"],
        recommends_proactive_contact=recommends,
        contact_reason="测试建议",
    )


@pytest.mark.parametrize(
    ("total", "grade"),
    [(59, Grade.C), (60, Grade.B), (69, Grade.B), (70, Grade.A), (79, Grade.A), (80, Grade.A), (100, Grade.A)],
)
def test_llm_score_boundaries(context: ScoringContext, total: int, grade: Grade) -> None:
    result = validate_llm_score(context, output_for_total(total))
    assert result.total_score == total
    assert result.grade == grade


def test_each_dimension_accepts_zero_and_full_score(context: ScoringContext) -> None:
    output = output_for_total(0)
    for item in output.dimensions:
        item.score = item.max_score
    output.total_score = 100
    result = validate_llm_score(context, output)
    assert result.total_score == 100


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "unknown",
        "score_overflow",
        "wrong_max",
        "invalid_evidence",
        "missing_evidence",
        "wrong_total",
        "excess_precision",
    ],
)
def test_invalid_llm_score_is_rejected(
    context: ScoringContext, mutation: str
) -> None:
    output = output_for_total(80)
    if mutation == "missing":
        output.dimensions.pop()
    elif mutation == "duplicate":
        output.dimensions[-1] = output.dimensions[0].model_copy()
    elif mutation == "unknown":
        output.dimensions[-1].dimension = "unknown"
    elif mutation == "score_overflow":
        output.dimensions[0].score = output.dimensions[0].max_score + 1
    elif mutation == "wrong_max":
        output.dimensions[0].max_score += 1
    elif mutation == "invalid_evidence":
        output.dimensions[0].evidence_refs = ["model.invented_fact"]
    elif mutation == "missing_evidence":
        output.dimensions[0].evidence_refs = []
    elif mutation == "wrong_total":
        output.total_score -= 1
    elif mutation == "excess_precision":
        output.dimensions[-1].score = Decimal("0.001")

    with pytest.raises(LlmScoreValidationError):
        validate_llm_score(context, output)


def test_hard_filter_cannot_be_overridden_by_model(context: ScoringContext) -> None:
    blocked_job = context.job.model_copy(update={"company_name": "黑名单公司"})
    result = validate_llm_score(
        context.model_copy(update={"job": blocked_job}),
        output_for_total(100),
    )
    assert result.eligibility == Eligibility.FILTERED_OUT
    assert result.hard_rejected is True
    assert is_automation_eligible(result, True) is False


@pytest.mark.parametrize(
    ("total", "recommends", "eligible"),
    [(79, True, False), (80, False, False), (80, True, True)],
)
def test_automation_requires_80_and_model_recommendation(
    context: ScoringContext, total: int, recommends: bool, eligible: bool
) -> None:
    result = validate_llm_score(context, output_for_total(total, recommends=recommends))
    assert is_automation_eligible(result, recommends) is eligible


def test_headhunter_score_cap_blocks_proactive_contact(context: ScoringContext) -> None:
    headhunter_context = context.model_copy(
        update={
            "parsed_job": context.parsed_job.model_copy(
                update={"headhunter_detected": True}
            ),
            "strategy": context.strategy.model_copy(
                update={"accept_headhunter": True, "headhunter_score_cap": 79}
            ),
        }
    )

    result = validate_llm_score(headhunter_context, output_for_total(100))

    assert result.hard_rejected is False
    assert result.total_score == 79
    assert sum(result.dimension_scores.values()) == 79
    assert result.action_blockers == ["HEADHUNTER_PROACTIVE_CONTACT_BLOCKED"]
    assert any("猎头岗位" in note for note in result.risk_notes)
    assert is_automation_eligible(result, True) is False


def test_headhunter_without_configured_cap_keeps_model_score(
    context: ScoringContext,
) -> None:
    headhunter_context = context.model_copy(
        update={
            "parsed_job": context.parsed_job.model_copy(
                update={"headhunter_detected": True}
            )
        }
    )

    result = validate_llm_score(headhunter_context, output_for_total(100))

    assert result.total_score == 100
