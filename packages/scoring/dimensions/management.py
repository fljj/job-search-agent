from decimal import Decimal

from packages.scoring.models import ScoreDetail, ScoringContext


def score_management(context: ScoringContext) -> ScoreDetail:
    parsed = context.parsed_job
    candidate = context.candidate
    if not parsed.management_required and not parsed.architecture_required:
        score = Decimal("2.5")
    else:
        score = Decimal(0)
        if parsed.management_required and candidate.management_years > 0:
            score += Decimal(3)
        if parsed.architecture_required and candidate.has_architecture_experience:
            score += Decimal(2)
    return ScoreDetail(dimension="management", score=score, max_score=Decimal(5),
                       rule_code="MANAGEMENT_CALCULATED",
                       explanation="根据管理、架构要求和候选人证据计分")
