import re
from enum import StrEnum

from pydantic import BaseModel, Field


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
    allowed_onsite_locations: list[str] = Field(default_factory=list)
    minimum_salary_k: float | None = None


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
    if any(
        keyword in combined
        for keyword in ("保险销售", "保险代理", "保险增员", "拉人头", "刷单", "交费入职")
    ):
        return QualificationStatus.MISMATCH, ["PROHIBITED_OR_FRAUD_DIRECTION"]
    if context.company_name and any(
        item.casefold() in context.company_name.casefold()
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
        context.work_mode == "ONSITE"
        and context.location
        and context.allowed_onsite_locations
        and not any(
            item.casefold() in context.location.casefold()
            for item in context.allowed_onsite_locations
        )
    ):
        return QualificationStatus.MISMATCH, ["LOCATION_CONFLICT"]
    if context.salary_text and context.minimum_salary_k is not None:
        values = [
            float(item)
            for item in re.findall(r"(\d+(?:\.\d+)?)\s*[kK]", context.salary_text)
        ]
        if values and max(values) < context.minimum_salary_k:
            return QualificationStatus.MISMATCH, ["SALARY_CONFLICT"]
    direction_known = bool(
        context.job_title
        and (
            not context.accepted_directions
            or any(
                direction.casefold().replace(" ", "")
                in context.job_title.casefold().replace(" ", "")
                or context.job_title.casefold().replace(" ", "")
                in direction.casefold().replace(" ", "")
                for direction in context.accepted_directions
            )
        )
    )
    message_direction = any(
        keyword in combined
        for keyword in (
            "java",
            "后端",
            "开发",
            "架构",
            "ai",
            "大模型",
            "vibe coding",
            "vibecoding",
            "直播运营",
        )
    )
    if context.job_title and context.accepted_directions and not direction_known:
        return QualificationStatus.MISMATCH, ["JOB_DIRECTION_CONFLICT"]
    if not direction_known and not message_direction:
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
    if complete:
        return QualificationStatus.FULL_MATCH, ["FULL_JOB_CONTEXT_AVAILABLE"]
    return QualificationStatus.ROUGH_MATCH, ["RELATED_DIRECTION_WITHOUT_CONFLICT"]
