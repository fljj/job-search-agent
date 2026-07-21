from decimal import Decimal

from packages.job_parser.normalizers import normalize_text
from packages.scoring.models import RuleType, ScoreDetail, ScoringContext


def score_title(context: ScoringContext) -> ScoreDetail:
    title = normalize_text(context.job.title)
    includes = [rule for rule in context.strategy.title_rules if rule.rule_type == RuleType.INCLUDE]
    matches = [rule for rule in includes if normalize_text(rule.pattern) in title]
    if matches:
        best = max(matches, key=lambda rule: rule.score)
        exact = title == normalize_text(best.pattern)
        score = best.score if exact else min(best.score, Decimal(12))
        return ScoreDetail(
            dimension="title", score=score, max_score=Decimal(15), rule_code="TITLE_MATCHED",
            explanation=f"职位名称匹配目标方向：{best.pattern}", matched_facts={"pattern": best.pattern},
        )
    if "后端" in title:
        return ScoreDetail(dimension="title", score=Decimal(9), max_score=Decimal(15),
                           rule_code="TITLE_FAMILY_MATCHED", explanation="职位属于后端岗位族")
    return ScoreDetail(dimension="title", score=Decimal(0), max_score=Decimal(15),
                       rule_code="TITLE_NOT_MATCHED", explanation="职位名称未匹配目标方向")
