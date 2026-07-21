from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class WorkMode(StrEnum):
    REMOTE = "REMOTE"
    ONSITE = "ONSITE"
    HYBRID = "HYBRID"
    UNKNOWN = "UNKNOWN"


class SourceJobStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class SeniorityLevel(StrEnum):
    INTERN = "INTERN"
    JUNIOR = "JUNIOR"
    MIDDLE = "MIDDLE"
    SENIOR = "SENIOR"
    LEAD = "LEAD"
    MANAGER = "MANAGER"
    ARCHITECT = "ARCHITECT"
    UNKNOWN = "UNKNOWN"


class SalaryRange(BaseModel):
    minimum_monthly_k: Decimal | None = Field(default=None, ge=0)
    maximum_monthly_k: Decimal | None = Field(default=None, ge=0)
    currency: str = "CNY"
    salary_months: int | None = Field(default=None, ge=1, le=24)
    negotiable: bool = False
    is_pre_tax: bool | None = None
    inferred_months: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> "SalaryRange":
        if (
            self.minimum_monthly_k is not None
            and self.maximum_monthly_k is not None
            and self.minimum_monthly_k > self.maximum_monthly_k
        ):
            raise ValueError("minimum_monthly_k 不能大于 maximum_monthly_k")
        return self


class JobInput(BaseModel):
    external_job_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)
    industry: str | None = None
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    salary_text: str | None = None
    description: str = Field(min_length=1)
    published_at: datetime | None = None
    source_status: SourceJobStatus = SourceJobStatus.UNKNOWN
    source: str = "MOCK"


class ParsedJob(BaseModel):
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    years_required: Decimal | None = Field(default=None, ge=0)
    management_required: bool = False
    architecture_required: bool = False
    seniority_level: SeniorityLevel = SeniorityLevel.UNKNOWN
    responsibilities: list[str] = Field(default_factory=list)
    salary: SalaryRange | None = None
    outsourcing_detected: bool = False
    headhunter_detected: bool = False
    internship_detected: bool = False
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    parser_type: str = "RULE"
    parser_version: str = "1.0.0"
