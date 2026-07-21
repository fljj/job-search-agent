from decimal import Decimal

from packages.job_parser.models import WorkMode
from packages.job_parser.normalizers import normalize_location
from packages.scoring.models import ScoreDetail, ScoringContext


def score_location(context: ScoringContext) -> ScoreDetail:
    mode = context.job.work_mode
    rule = next((item for item in context.strategy.work_mode_rules if item.work_mode == mode), None)
    if mode == WorkMode.UNKNOWN:
        unknown_rule = next((item for item in context.strategy.work_mode_rules
                             if item.work_mode == WorkMode.UNKNOWN), None)
        score = unknown_rule.unknown_score if unknown_rule else Decimal(8)
        return ScoreDetail(dimension="location", score=score, max_score=Decimal(15),
                           rule_code="WORK_MODE_UNKNOWN", explanation="工作模式未知，需进一步确认")
    if rule is None or not rule.enabled:
        return ScoreDetail(dimension="location", score=Decimal(0), max_score=Decimal(15),
                           rule_code="WORK_MODE_DISABLED", explanation="策略未启用该工作模式")
    if mode == WorkMode.REMOTE or not rule.location_restricted:
        return ScoreDetail(dimension="location", score=rule.score, max_score=Decimal(15),
                           rule_code="LOCATION_MATCHED", explanation="工作模式和地点符合策略")
    location = normalize_location(context.job.location)
    allowed = {normalize_location(item) for item in rule.allowed_locations}
    score = rule.score if location in allowed else Decimal(0)
    return ScoreDetail(dimension="location", score=score, max_score=Decimal(15),
                       rule_code="LOCATION_MATCHED" if score else "LOCATION_NOT_ALLOWED",
                       explanation="工作地点符合策略" if score else "工作地点不在允许范围")
