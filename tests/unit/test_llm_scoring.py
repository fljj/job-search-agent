from decimal import Decimal

import pytest

from packages.llm.models import JobScoreOutput, ScoreDimension
from packages.scoring.evidence import (
    evidence_catalog,
    scoring_context_from_snapshot,
    with_evidence_catalog,
)
from packages.scoring.llm_engine import (
    LlmScoreValidationError,
    is_automation_eligible,
    validate_llm_score,
)
from packages.scoring.models import DIMENSION_MAX, Eligibility, Grade, ScoringContext


def output_for_total(
    context: ScoringContext, total: int, *, recommends: bool = True
) -> JobScoreOutput:
    catalog = evidence_catalog(context)
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
                evidence_refs=[
                    next(
                        item.id
                        for item in catalog.values()
                        if dimension in item.dimensions
                    )
                ],
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
def test_llm_score_boundaries(
    evidence_context: ScoringContext, total: int, grade: Grade
) -> None:
    result = validate_llm_score(
        evidence_context, output_for_total(evidence_context, total)
    )
    assert result.total_score == total
    assert result.grade == grade


def test_each_dimension_accepts_zero_and_full_score(
    evidence_context: ScoringContext,
) -> None:
    output = output_for_total(evidence_context, 0)
    for item in output.dimensions:
        item.score = item.max_score
    output.total_score = 100
    result = validate_llm_score(evidence_context, output)
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
        "duplicate_evidence",
        "wrong_total",
        "excess_precision",
    ],
)
def test_invalid_llm_score_is_rejected(
    evidence_context: ScoringContext, mutation: str
) -> None:
    output = output_for_total(evidence_context, 80)
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
    elif mutation == "duplicate_evidence":
        output.dimensions[0].evidence_refs *= 2
    elif mutation == "wrong_total":
        output.total_score -= 1
    elif mutation == "excess_precision":
        output.dimensions[-1].score = Decimal("0.001")

    with pytest.raises(LlmScoreValidationError):
        validate_llm_score(evidence_context, output)


def test_hard_filter_cannot_be_overridden_by_model(
    evidence_context: ScoringContext,
) -> None:
    blocked_job = evidence_context.job.model_copy(
        update={"company_name": "黑名单公司"}
    )
    blocked_context = with_evidence_catalog(
        evidence_context.model_copy(update={"job": blocked_job})
    )
    result = validate_llm_score(
        blocked_context,
        output_for_total(blocked_context, 100),
    )
    assert result.eligibility == Eligibility.FILTERED_OUT
    assert result.hard_rejected is True
    assert is_automation_eligible(result, True) is False


@pytest.mark.parametrize(
    ("total", "recommends", "eligible"),
    [(79, True, False), (80, False, False), (80, True, True)],
)
def test_automation_requires_80_and_model_recommendation(
    evidence_context: ScoringContext,
    total: int,
    recommends: bool,
    eligible: bool,
) -> None:
    result = validate_llm_score(
        evidence_context,
        output_for_total(evidence_context, total, recommends=recommends),
    )
    assert is_automation_eligible(result, recommends) is eligible


def test_headhunter_score_cap_blocks_proactive_contact(
    evidence_context: ScoringContext,
) -> None:
    headhunter_context = with_evidence_catalog(evidence_context.model_copy(
        update={
            "parsed_job": evidence_context.parsed_job.model_copy(
                update={"headhunter_detected": True}
            ),
            "strategy": evidence_context.strategy.model_copy(
                update={"accept_headhunter": True, "headhunter_score_cap": 79}
            ),
        }
    ))

    result = validate_llm_score(
        headhunter_context, output_for_total(headhunter_context, 100)
    )

    assert result.hard_rejected is False
    assert result.total_score == 79
    assert sum(result.dimension_scores.values()) == 79
    assert result.action_blockers == ["HEADHUNTER_PROACTIVE_CONTACT_BLOCKED"]
    assert any("猎头岗位" in note for note in result.risk_notes)
    assert is_automation_eligible(result, True) is False


def test_headhunter_without_configured_cap_keeps_model_score(
    evidence_context: ScoringContext,
) -> None:
    headhunter_context = with_evidence_catalog(evidence_context.model_copy(
        update={
            "parsed_job": evidence_context.parsed_job.model_copy(
                update={"headhunter_detected": True}
            )
        }
    ))

    result = validate_llm_score(
        headhunter_context, output_for_total(headhunter_context, 100)
    )

    assert result.total_score == 100


def test_evidence_ids_are_stable_when_list_order_changes(
    context: ScoringContext,
) -> None:
    first = with_evidence_catalog(context)
    reordered_candidate = context.candidate.model_copy(
        update={"skills": list(reversed(context.candidate.skills))}
    )
    second = with_evidence_catalog(
        context.model_copy(update={"candidate": reordered_candidate})
    )

    assert first.evidence_items == second.evidence_items


def test_specific_skill_evidence_is_resolved_in_score_detail(
    evidence_context: ScoringContext,
) -> None:
    output = output_for_total(evidence_context, 80)
    java = next(
        item
        for item in evidence_context.evidence_items
        if item.source_path == "candidate.skills"
        and isinstance(item.value, dict)
        and item.value.get("name") == "Java"
    )
    skills = next(item for item in output.dimensions if item.dimension == "skills")
    skills.evidence_refs = [java.id]

    result = validate_llm_score(evidence_context, output)
    detail = next(item for item in result.details if item.dimension == "skills")

    assert detail.matched_facts["evidence_items"] == [java.model_dump(mode="json")]


def test_cross_dimension_evidence_is_rejected(
    evidence_context: ScoringContext,
) -> None:
    output = output_for_total(evidence_context, 80)
    title_reference = next(
        item.id
        for item in evidence_context.evidence_items
        if item.dimensions == ["title"]
    )
    skills = next(item for item in output.dimensions if item.dimension == "skills")
    skills.evidence_refs = [title_reference]

    with pytest.raises(LlmScoreValidationError, match="跨维度"):
        validate_llm_score(evidence_context, output)


def test_stale_evidence_catalog_is_rejected(
    evidence_context: ScoringContext,
) -> None:
    changed_candidate = evidence_context.candidate.model_copy(
        update={"skills": evidence_context.candidate.skills[:-1]}
    )
    stale_context = evidence_context.model_copy(
        update={"candidate": changed_candidate}
    )

    with pytest.raises(LlmScoreValidationError, match="当前输入快照"):
        validate_llm_score(
            stale_context, output_for_total(evidence_context, 80)
        )


def test_historical_snapshot_restores_validated_evidence_context(
    evidence_context: ScoringContext,
) -> None:
    restored = scoring_context_from_snapshot(
        evidence_context.model_dump(mode="json")
    )

    assert restored == evidence_context
