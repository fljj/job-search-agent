from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from packages.job_parser.models import JobInput, ParsedJob, SeniorityLevel, WorkMode


class RuleType(StrEnum):
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"


class IndustryRuleType(StrEnum):
    PREFERRED = "PREFERRED"
    ACCEPTABLE = "ACCEPTABLE"
    EXCLUDED = "EXCLUDED"


class ContactDecision(StrEnum):
    CONTACT = "CONTACT"
    SKIP = "SKIP"
    REVIEW = "REVIEW"
    FILTERED_OUT = "FILTERED_OUT"


class EffectiveJobStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class CandidateSkill(BaseModel):
    name: str
    years: Decimal | None = Field(default=None, ge=0)
    source: str
    is_core: bool = False


class CandidateProfile(BaseModel):
    name: str
    total_years: Decimal = Field(ge=0)
    management_years: Decimal = Field(default=Decimal(0), ge=0)
    has_architecture_experience: bool = False
    has_core_system_experience: bool = False
    bachelor_full_time: bool | None = None
    skills: list[CandidateSkill] = Field(default_factory=list)
    industry_experiences: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)


class TitleRule(BaseModel):
    rule_type: RuleType
    pattern: str


class WorkModeRule(BaseModel):
    work_mode: WorkMode
    enabled: bool = True
    allowed_locations: list[str] = Field(default_factory=list)
    location_restricted: bool = False


class SalaryRule(BaseModel):
    work_mode: WorkMode
    currency: str = "CNY"
    minimum_monthly_k: Decimal = Field(ge=0)
    expected_monthly_k: Decimal = Field(ge=0)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    exchange_rate_version: str | None = None

    @model_validator(mode="after")
    def validate_rule(self) -> "SalaryRule":
        if self.minimum_monthly_k > self.expected_monthly_k:
            raise ValueError("最低薪资不能高于期望薪资")
        return self


class IndustryRule(BaseModel):
    industry: str
    rule_type: IndustryRuleType


class Strategy(BaseModel):
    name: str
    enabled: bool = True
    priority: int = Field(default=100, ge=1)
    title_rules: list[TitleRule]
    accepted_seniority_levels: list[SeniorityLevel] = Field(default_factory=list)
    work_mode_rules: list[WorkModeRule]
    salary_rules: list[SalaryRule]
    industry_rules: list[IndustryRule]
    company_blacklist: list[str] = Field(default_factory=list)
    accept_outsourcing: bool = False
    accept_part_time: bool = False
    accept_headhunter: bool = True
    max_posted_days: int = Field(default=30, ge=1)
    core_required_skills: list[str] = Field(default_factory=list)
    arrival_time_reply: str | None = Field(default=None, max_length=200)
    reject_full_time_bachelor_required: bool = False
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_unique_rules(self) -> "Strategy":
        modes = [rule.work_mode for rule in self.work_mode_rules]
        if len(modes) != len(set(modes)):
            raise ValueError("同一工作模式只能配置一条地点规则")
        salary_modes = [rule.work_mode for rule in self.salary_rules]
        if len(salary_modes) != len(set(salary_modes)):
            raise ValueError("同一工作模式只能配置一条薪资规则")
        industries = [rule.industry.lower() for rule in self.industry_rules]
        if len(industries) != len(set(industries)):
            raise ValueError("同一行业只能配置一条规则")
        return self


class HardRejectionReason(BaseModel):
    rule_code: str
    message: str
    evidence: dict[str, object] = Field(default_factory=dict)


class JobDecisionContext(BaseModel):
    job: JobInput
    parsed_job: ParsedJob
    candidate: CandidateProfile
    strategy: Strategy


class JobDecisionResult(BaseModel):
    decision: ContactDecision
    confidence: Decimal = Field(ge=0, le=1)
    hard_rejected: bool
    effective_job_status: EffectiveJobStatus
    action_blockers: list[str] = Field(default_factory=list)
    rejection_reasons: list[HardRejectionReason] = Field(default_factory=list)
    matched_evidence: list[str] = Field(default_factory=list, max_length=3)
    uncertainties: list[str] = Field(default_factory=list, max_length=3)
    reason: str
    automation_eligible: bool = False
    decision_version: str
