from decimal import Decimal

from packages.job_parser.normalizers import normalize_text
from packages.scoring.models import IndustryRuleType, ScoreDetail, ScoringContext


def score_industry(context: ScoringContext) -> ScoreDetail:
    industry = normalize_text(context.job.industry or "")
    matches = [rule for rule in context.strategy.industry_rules
               if normalize_text(rule.industry) in industry or industry in normalize_text(rule.industry)] if industry else []
    excluded = next((rule for rule in matches if rule.rule_type == IndustryRuleType.EXCLUDED), None)
    if excluded:
        return ScoreDetail(dimension="industry", score=Decimal(0), max_score=Decimal(10),
                           rule_code="INDUSTRY_EXCLUDED", explanation=f"行业被策略排除：{excluded.industry}")
    if matches:
        best = max(matches, key=lambda rule: rule.score)
        return ScoreDetail(dimension="industry", score=best.score, max_score=Decimal(10),
                           rule_code="INDUSTRY_MATCHED", explanation=f"行业匹配：{best.industry}")
    return ScoreDetail(dimension="industry", score=Decimal(4), max_score=Decimal(10),
                       rule_code="INDUSTRY_UNKNOWN", explanation="行业未配置或无法识别")
