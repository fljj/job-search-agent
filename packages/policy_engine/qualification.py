import re
from enum import StrEnum

from pydantic import BaseModel, Field

from packages.job_parser.normalizers import (
    location_matches_allowed,
    normalize_company,
    parse_salary,
)


class QualificationStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    ROUGH_MATCH = "ROUGH_MATCH"
    FULL_MATCH = "FULL_MATCH"
    MISMATCH = "MISMATCH"


class QualificationContext(BaseModel):
    company_name: str | None = None
    job_title: str | None = None
    industry: str | None = None
    location: str | None = None
    work_mode: str | None = None
    salary_text: str | None = None
    description: str | None = None
    message_text: str = ""
    accepted_directions: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    blacklisted_companies: list[str] = Field(default_factory=list)
    enabled_work_modes: list[str] = Field(default_factory=list)
    allowed_locations: list[str] = Field(default_factory=list)
    salary_threshold_k: float | None = None
    prohibited_direction_keywords: list[str] = Field(default_factory=list)
    related_direction_keywords: list[str] = Field(default_factory=list)


def evaluate_qualification(
    context: QualificationContext,
) -> tuple[QualificationStatus, list[str]]:
    """基于已知结构化事实判断入站推进成熟度，不生成职位分数。"""
    combined = " ".join(
        item
        for item in (
            context.company_name,
            context.job_title,
            context.industry,
            context.description,
            context.message_text,
        )
        if item
    ).casefold()
    if any(keyword.casefold() in combined for keyword in context.prohibited_direction_keywords):
        return QualificationStatus.MISMATCH, ["PROHIBITED_OR_FRAUD_DIRECTION"]
    if context.company_name and any(
        normalize_company(item) == normalize_company(context.company_name)
        for item in context.blacklisted_companies
    ):
        return QualificationStatus.MISMATCH, ["COMPANY_BLACKLISTED"]
    if context.industry and any(
        item.casefold() in context.industry.casefold()
        for item in context.excluded_industries
    ):
        return QualificationStatus.MISMATCH, ["INDUSTRY_EXCLUDED"]
    if (
        context.work_mode
        and context.work_mode not in {"UNKNOWN", ""}
        and context.enabled_work_modes
        and context.work_mode not in context.enabled_work_modes
    ):
        return QualificationStatus.MISMATCH, ["WORK_MODE_CONFLICT"]
    if (
        context.work_mode in {"ONSITE", "HYBRID"}
        and context.location
        and context.allowed_locations
        and not location_matches_allowed(context.location, context.allowed_locations)
    ):
        return QualificationStatus.MISMATCH, ["LOCATION_CONFLICT"]
    if context.salary_text and context.salary_threshold_k is not None:
        salary = parse_salary(context.salary_text)
        if (
            salary
            and salary.maximum_monthly_k is not None
            and float(salary.maximum_monthly_k) < context.salary_threshold_k
        ):
            return QualificationStatus.MISMATCH, ["SALARY_CONFLICT"]
    structured_direction_known = bool(
        context.job_title
        and _matches_accepted_direction(
            context.job_title,
            context.description,
            context.accepted_directions,
            context.related_direction_keywords,
        )
    )
    normalized_message = context.message_text.casefold().replace(" ", "")
    message_direction_known = any(
        direction.casefold().replace(" ", "") in normalized_message
        for direction in context.accepted_directions
    ) or any(
        keyword.casefold().replace(" ", "") in normalized_message
        for keyword in context.related_direction_keywords
    )
    if (
        context.job_title
        and context.accepted_directions
        and not structured_direction_known
        and not message_direction_known
    ):
        return QualificationStatus.MISMATCH, ["JOB_DIRECTION_CONFLICT"]
    if not structured_direction_known and not message_direction_known:
        return QualificationStatus.UNKNOWN, ["JOB_DIRECTION_UNKNOWN"]
    complete = all(
        (
            context.company_name,
            context.job_title,
            context.location,
            context.work_mode and context.work_mode != "UNKNOWN",
            context.salary_text,
            context.description,
        )
    )
    if complete and structured_direction_known:
        return QualificationStatus.FULL_MATCH, ["FULL_JOB_CONTEXT_AVAILABLE"]
    return QualificationStatus.ROUGH_MATCH, ["RELATED_DIRECTION_WITHOUT_CONFLICT"]


def _matches_accepted_direction(
    job_title: str,
    description: str | None,
    accepted_directions: list[str],
    related_direction_keywords: list[str],
) -> bool:
    if not accepted_directions:
        return True
    normalized_title = job_title.casefold().replace(" ", "")
    if any(
        direction.casefold().replace(" ", "") in normalized_title
        or normalized_title in direction.casefold().replace(" ", "")
        for direction in accepted_directions
    ):
        return True

    # “AI应用开发工程师（JAVA）”与“Java后端”等写法没有完整子串关系，
    # 但标题或 JD 中明确出现相同技术方向时，不能判定为方向冲突。
    title_terms = set(re.findall(r"[a-z][a-z0-9+#.-]*", job_title.casefold()))
    accepted_terms = {
        term
        for direction in accepted_directions
        for term in re.findall(r"[a-z][a-z0-9+#.-]*", direction.casefold())
    }
    if title_terms & accepted_terms:
        return True

    normalized_title_with_spaces = job_title.casefold().replace(" ", "")
    title_is_related = any(
        keyword.casefold().replace(" ", "") in normalized_title_with_spaces
        for keyword in related_direction_keywords
    )
    if not title_is_related or not description:
        return False
    description_terms = set(
        re.findall(r"[a-z][a-z0-9+#.-]*", description.casefold())
    )
    return bool(description_terms & accepted_terms)
