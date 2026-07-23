import json
from functools import lru_cache
from pathlib import Path

from packages.policy_engine.recommendation import RecommendationRules


@lru_cache
def get_recommendation_rules() -> RecommendationRules:
    path = Path(__file__).parents[4] / "config" / "recommendation-policy.json"
    return RecommendationRules.model_validate(json.loads(path.read_text(encoding="utf-8")))
