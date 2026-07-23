from pydantic import BaseModel

from packages.scoring.models import ScoreResult, Strategy


class StrategyScoreCandidate(BaseModel):
    strategy: Strategy
    score: ScoreResult


def select_best_strategy(
    candidates: list[StrategyScoreCandidate],
) -> StrategyScoreCandidate:
    """选择未排除的最高分策略；同分按优先级，全部排除时仅按优先级。"""
    enabled = [item for item in candidates if item.strategy.enabled]
    if not enabled:
        raise ValueError("没有可用的启用策略评分")
    eligible = [item for item in enabled if not item.score.hard_rejected]
    if eligible:
        return min(
            eligible,
            key=lambda item: (-item.score.total_score, item.strategy.priority),
        )
    return min(enabled, key=lambda item: item.strategy.priority)
