from decimal import Decimal

from packages.scoring.models import ScoreDetail, ScoringContext


def score_experience(context: ScoringContext) -> ScoreDetail:
    required = context.parsed_job.years_required
    if required is None:
        years_score = Decimal(6)
        code = "YEARS_UNKNOWN"
    else:
        gap = required - context.candidate.total_years
        if gap <= 0:
            years_score = Decimal(9)
        elif gap <= 1:
            years_score = Decimal(7)
        elif gap <= 3:
            years_score = Decimal(4)
        else:
            years_score = Decimal(1)
        code = "YEARS_CALCULATED"
    core_score = Decimal(3) if context.candidate.has_core_system_experience else Decimal(0)
    industry = (context.job.industry or "").lower()
    industry_match = any(item.lower() in industry or industry in item.lower()
                         for item in context.candidate.industry_experiences if industry)
    industry_score = Decimal(3) if industry_match else Decimal(0)
    score = years_score + core_score + industry_score
    return ScoreDetail(dimension="experience", score=score, max_score=Decimal(15), rule_code=code,
                       explanation="根据相关年限、核心系统和行业经历计分")
