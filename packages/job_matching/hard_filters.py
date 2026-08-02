import re

from packages.job_matching.models import (
    HardRejectionReason,
    IndustryRuleType,
    JobDecisionContext,
    RuleType,
)
from packages.job_parser.models import SeniorityLevel, WorkMode
from packages.job_parser.normalizers import (
    normalize_company,
    normalize_location,
    normalize_skill,
    normalize_text,
)


def evaluate_hard_filters(
    context: JobDecisionContext,
    *,
    direction_keywords: list[str] | None = None,
) -> list[HardRejectionReason]:
    reasons: list[HardRejectionReason] = []
    job = context.job
    parsed = context.parsed_job
    strategy = context.strategy
    title = normalize_text(job.title)

    if direction_keywords and not _has_direction_evidence(
        context, direction_keywords
    ):
        reasons.append(
            _reason(
                "JOB_DIRECTION_CONFLICT",
                "职位完整信息未发现求职方向相关证据",
                {"title": job.title},
            )
        )

    if any(
        normalize_text(rule.pattern) in title
        for rule in strategy.title_rules
        if rule.rule_type == RuleType.EXCLUDE
    ):
        reasons.append(
            _reason("TITLE_EXCLUDED", "职位名称命中排除规则", {"title": job.title})
        )

    mode_rule = next(
        (
            rule
            for rule in strategy.work_mode_rules
            if rule.work_mode == job.work_mode
        ),
        None,
    )
    if job.work_mode != WorkMode.UNKNOWN and (
        mode_rule is None or not mode_rule.enabled
    ):
        reasons.append(
            _reason(
                "WORK_MODE_DISABLED",
                "策略未启用该工作模式",
                {"work_mode": job.work_mode.value},
            )
        )
    elif (
        mode_rule
        and mode_rule.location_restricted
        and job.work_mode in {WorkMode.ONSITE, WorkMode.HYBRID}
        and not (
            parsed.part_time_detected
            and strategy.accept_part_time
            and not parsed.onsite_required_explicitly
        )
    ):
        location = normalize_location(job.location)
        allowed = {
            normalize_location(item) for item in mode_rule.allowed_locations
        }
        if location not in allowed:
            code = (
                "ONSITE_LOCATION_NOT_ALLOWED"
                if job.work_mode == WorkMode.ONSITE
                else "HYBRID_LOCATION_NOT_ALLOWED"
            )
            reasons.append(
                _reason(
                    code,
                    "工作地点不在策略允许范围",
                    {
                        "location": job.location,
                        "allowed": mode_rule.allowed_locations,
                    },
                )
            )

    owned = {
        normalize_skill(skill.name).lower() for skill in context.candidate.skills
    }
    core_required = {
        normalize_skill(skill).lower()
        for skill in strategy.core_required_skills
    }
    job_required = {
        normalize_skill(skill).lower() for skill in parsed.required_skills
    }
    relevant_core = core_required & job_required
    if relevant_core and relevant_core.isdisjoint(owned):
        reasons.append(
            _reason(
                "REQUIRED_CORE_SKILL_MISSING",
                "候选人完全缺失职位核心硬门槛技能",
                {"required_core_skills": sorted(relevant_core)},
            )
        )

    salary_rule = next(
        (
            rule
            for rule in strategy.salary_rules
            if rule.work_mode == job.work_mode
        ),
        None,
    )
    salary = parsed.salary
    if (
        salary_rule
        and salary
        and not salary.negotiable
        and salary.is_pre_tax is not False
        and salary.maximum_monthly_k is not None
        and salary.maximum_monthly_k < salary_rule.minimum_monthly_k
    ):
        reasons.append(
            _reason(
                "SALARY_BELOW_MINIMUM",
                "薪资上限低于策略最低接受值",
                {
                    "maximum_monthly_k": str(salary.maximum_monthly_k),
                    "minimum_monthly_k": str(
                        salary_rule.minimum_monthly_k
                    ),
                },
            )
        )

    industry = normalize_text(job.industry or "")
    excluded_industries = [
        rule.industry
        for rule in strategy.industry_rules
        if rule.rule_type == IndustryRuleType.EXCLUDED
        and industry
        and normalize_text(rule.industry) in industry
    ]
    if excluded_industries:
        reasons.append(
            _reason(
                "INDUSTRY_EXCLUDED",
                "职位行业被策略排除",
                {"industries": excluded_industries},
            )
        )

    company = normalize_company(job.company_name)
    if company in {
        normalize_company(item) for item in strategy.company_blacklist
    }:
        reasons.append(
            _reason(
                "COMPANY_BLACKLISTED",
                "公司命中策略黑名单",
                {"company_name": job.company_name},
            )
        )
    if parsed.outsourcing_detected and not strategy.accept_outsourcing:
        reasons.append(
            _reason("OUTSOURCING_NOT_ACCEPTED", "策略不接受纯人力外包岗位")
        )
    if parsed.part_time_detected and not strategy.accept_part_time:
        reasons.append(_reason("PART_TIME_NOT_ACCEPTED", "策略不接受兼职岗位"))
    if parsed.headhunter_detected and not strategy.accept_headhunter:
        reasons.append(_reason("HEADHUNTER_NOT_ACCEPTED", "策略不接受猎头职位"))
    if (
        parsed.internship_detected
        or parsed.seniority_level == SeniorityLevel.INTERN
    ):
        reasons.append(_reason("INTERNSHIP_POSITION", "实习岗位不符合策略"))
    if (
        parsed.full_time_bachelor_required
        and strategy.reject_full_time_bachelor_required
        and context.candidate.bachelor_full_time is False
    ):
        reasons.append(
            _reason(
                "FULL_TIME_BACHELOR_REQUIRED",
                "职位明确要求全日制本科，候选人学历形式不符合",
                {"requirement": "全日制本科"},
            )
        )
    if (
        parsed.seniority_level == SeniorityLevel.JUNIOR
        and SeniorityLevel.JUNIOR not in strategy.accepted_seniority_levels
    ):
        reasons.append(_reason("JUNIOR_POSITION", "策略不接受初级岗位"))
    return reasons


def _has_direction_evidence(
    context: JobDecisionContext,
    direction_keywords: list[str],
) -> bool:
    """只在完整 JD 毫无正向证据时排除，避免标题不典型的研发岗被误伤。"""
    job = context.job
    parsed = context.parsed_job
    text = "\n".join(
        [job.title, job.description, *parsed.responsibilities]
    )
    if any(_contains_keyword(text, keyword) for keyword in direction_keywords):
        return True

    candidate_skills = {
        normalize_skill(skill.name).casefold() for skill in context.candidate.skills
    }
    job_skills = {
        normalize_skill(skill).casefold()
        for skill in [*parsed.required_skills, *parsed.preferred_skills]
    }
    return bool(candidate_skills & job_skills)


def _contains_keyword(text: str, keyword: str) -> bool:
    normalized_keyword = "".join(keyword.casefold().split())
    if not normalized_keyword:
        return False
    if normalized_keyword.isascii():
        return (
            re.search(
                rf"(?<![a-z0-9]){re.escape(keyword.casefold().strip())}(?![a-z0-9])",
                text.casefold(),
            )
            is not None
        )
    return normalized_keyword in "".join(text.casefold().split())


def _reason(
    code: str,
    message: str,
    evidence: dict[str, object] | None = None,
) -> HardRejectionReason:
    return HardRejectionReason(
        rule_code=code,
        message=message,
        evidence=evidence or {},
    )
