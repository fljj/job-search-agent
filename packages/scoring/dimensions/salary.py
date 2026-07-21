from decimal import Decimal

from packages.scoring.models import InterpolationType, ScoreDetail, ScoringContext


def score_salary(context: ScoringContext) -> ScoreDetail:
    rule = next((item for item in context.strategy.salary_rules
                 if item.work_mode == context.job.work_mode), None)
    salary = context.parsed_job.salary
    if rule is None:
        return ScoreDetail(dimension="salary", score=Decimal(0), max_score=Decimal(15),
                           rule_code="SALARY_RULE_MISSING", explanation="未配置对应工作模式的薪资规则")
    if salary is None:
        return ScoreDetail(dimension="salary", score=rule.unknown_score, max_score=Decimal(15),
                           rule_code="SALARY_UNKNOWN", explanation="薪资无法规范化")
    if salary.negotiable:
        return ScoreDetail(dimension="salary", score=rule.negotiable_score, max_score=Decimal(15),
                           rule_code="SALARY_NEGOTIABLE", explanation="薪资面议")
    if salary.is_pre_tax is False or salary.minimum_monthly_k is None or salary.maximum_monthly_k is None:
        return ScoreDetail(dimension="salary", score=rule.unknown_score, max_score=Decimal(15),
                           rule_code="SALARY_UNCERTAIN", explanation="薪资属性不足以可靠比较")
    midpoint = (salary.minimum_monthly_k + salary.maximum_monthly_k) / 2
    band = next((item for item in rule.bands if midpoint >= item.lower_bound_k
                 and (item.upper_bound_k is None or midpoint < item.upper_bound_k)), None)
    if band is None:
        score = Decimal(0)
    elif band.interpolation == InterpolationType.STEP or band.upper_bound_k is None:
        score = band.max_score
    else:
        ratio = (midpoint - band.lower_bound_k) / (band.upper_bound_k - band.lower_bound_k)
        score = band.min_score + ratio * (band.max_score - band.min_score)
    return ScoreDetail(dimension="salary", score=score.quantize(Decimal("0.01")),
                       max_score=Decimal(15), rule_code="SALARY_CALCULATED",
                       explanation=f"按月薪区间中点 {midpoint}K 计分")
