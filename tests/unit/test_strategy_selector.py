from decimal import Decimal

from packages.llm.models import JobScoreOutput, ScoreDimension
from packages.scoring.llm_engine import EVIDENCE_REFS, validate_llm_score
from packages.scoring.models import DIMENSION_MAX, ScoringContext
from packages.scoring.strategy_selector import (
    StrategyScoreCandidate,
    select_best_strategy,
)


def output_for_total(total: int) -> JobScoreOutput:
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
                reason="test",
                evidence_refs=[sorted(EVIDENCE_REFS[dimension])[0]],
            )
        )
    return JobScoreOutput(
        dimensions=dimensions,
        total_score=total,
        recommends_proactive_contact=True,
        contact_reason="test",
    )


def candidate(context: ScoringContext, score: int, priority: int) -> StrategyScoreCandidate:
    strategy = context.strategy.model_copy(update={"priority": priority})
    result = validate_llm_score(
        context.model_copy(update={"strategy": strategy}),
        output_for_total(score),
    )
    return StrategyScoreCandidate(strategy=strategy, score=result)


def test_selects_highest_non_rejected_score(context: ScoringContext) -> None:
    selected = select_best_strategy([candidate(context, 70, 1), candidate(context, 80, 10)])
    assert selected.score.total_score == 80


def test_same_score_uses_strategy_priority(context: ScoringContext) -> None:
    selected = select_best_strategy([candidate(context, 80, 20), candidate(context, 80, 2)])
    assert selected.strategy.priority == 2


def test_disabled_strategy_is_not_selected(context: ScoringContext) -> None:
    disabled = candidate(context, 100, 1)
    disabled.strategy.enabled = False
    selected = select_best_strategy([disabled, candidate(context, 70, 2)])
    assert selected.score.total_score == 70


def test_all_rejected_uses_highest_priority(context: ScoringContext) -> None:
    blocked_context = context.model_copy(
        update={"job": context.job.model_copy(update={"company_name": "黑名单公司"})}
    )
    first = candidate(blocked_context, 100, 20)
    second = candidate(blocked_context, 60, 2)
    selected = select_best_strategy([first, second])
    assert selected.strategy.priority == 2
