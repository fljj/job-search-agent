from decimal import Decimal

from packages.job_parser.normalizers import normalize_skill
from packages.scoring.models import ScoreDetail, ScoringContext


def score_skills(context: ScoringContext) -> ScoreDetail:
    owned = {normalize_skill(skill.name).lower() for skill in context.candidate.skills}
    required = [normalize_skill(skill) for skill in context.parsed_job.required_skills]
    preferred = [normalize_skill(skill) for skill in context.parsed_job.preferred_skills]
    matched_required = [skill for skill in required if skill.lower() in owned]
    matched_preferred = [skill for skill in preferred if skill.lower() in owned]
    if not required and not preferred:
        return ScoreDetail(dimension="skills", score=Decimal("12.5"), max_score=Decimal(25),
                           rule_code="SKILLS_UNKNOWN", explanation="JD 未提供明确技能要求")
    required_ratio = Decimal(len(matched_required)) / len(required) if required else Decimal(0)
    preferred_ratio = Decimal(len(matched_preferred)) / len(preferred) if preferred else Decimal(0)
    if required and preferred:
        score = Decimal(25) * (Decimal("0.8") * required_ratio + Decimal("0.2") * preferred_ratio)
    elif required:
        score = Decimal(25) * required_ratio
    else:
        score = Decimal("12.5") * preferred_ratio
    return ScoreDetail(
        dimension="skills", score=score.quantize(Decimal("0.01")), max_score=Decimal(25),
        rule_code="SKILLS_CALCULATED", explanation="按必须技能和加分技能匹配比例计分",
        matched_facts={"matched_required": matched_required, "matched_preferred": matched_preferred},
    )
