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


class InterpolationType(StrEnum):
    STEP = "STEP"
    LINEAR = "LINEAR"


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class Eligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
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
    score: Decimal = Field(default=Decimal(15), ge=0, le=15)
    is_hard_requirement: bool = False


class WorkModeRule(BaseModel):
    work_mode: WorkMode
    enabled: bool = True
    allowed_locations: list[str] = Field(default_factory=list)
    location_restricted: bool = False
    score: Decimal = Field(default=Decimal(15), ge=0, le=15)
    unknown_score: Decimal = Field(default=Decimal(8), ge=0, le=15)


class SalaryBand(BaseModel):
    lower_bound_k: Decimal = Field(ge=0)
    upper_bound_k: Decimal | None = Field(default=None, ge=0)
    min_score: Decimal = Field(ge=0, le=15)
    max_score: Decimal = Field(ge=0, le=15)
    interpolation: InterpolationType = InterpolationType.STEP

    @model_validator(mode="after")
    def validate_band(self) -> "SalaryBand":
        if self.upper_bound_k is not None and self.lower_bound_k >= self.upper_bound_k:
            raise ValueError("薪资区间上限必须大于下限")
        if self.min_score > self.max_score:
            raise ValueError("min_score 不能大于 max_score")
        return self


class SalaryRule(BaseModel):
    work_mode: WorkMode
    currency: str = "CNY"
    minimum_monthly_k: Decimal = Field(ge=0)
    expected_monthly_k: Decimal = Field(ge=0)
    negotiable_score: Decimal = Field(default=Decimal(8), ge=0, le=15)
    unknown_score: Decimal = Field(default=Decimal(8), ge=0, le=15)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    exchange_rate_version: str | None = None
    bands: list[SalaryBand]

    @model_validator(mode="after")
    def validate_rule(self) -> "SalaryRule":
        if self.minimum_monthly_k > self.expected_monthly_k:
            raise ValueError("最低薪资不能高于期望薪资")
        ordered = sorted(self.bands, key=lambda item: item.lower_bound_k)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.upper_bound_k is None or previous.upper_bound_k > current.lower_bound_k:
                raise ValueError("薪资计分区间不能重叠")
        return self


class IndustryRule(BaseModel):
    industry: str
    rule_type: IndustryRuleType
    score: Decimal = Field(ge=0, le=10)


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
    accept_headhunter: bool = True
    headhunter_score_cap: int | None = Field(default=None, ge=0, le=79)
    max_posted_days: int = Field(default=30, ge=1)
    core_required_skills: list[str] = Field(default_factory=list)
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


class ScoreDetail(BaseModel):
    dimension: str
    score: Decimal
    max_score: Decimal
    rule_code: str
    explanation: str
    evidence_refs: list[str] = Field(default_factory=list)
    matched_facts: dict[str, object] = Field(default_factory=dict)


class RejectionReason(BaseModel):
    rule_code: str
    message: str
    evidence: dict[str, object] = Field(default_factory=dict)


class ScoringEvidenceItem(BaseModel):
    id: str = Field(pattern=r"^evidence:[0-9a-f]{64}$")
    source_path: str
    value: object
    dimensions: list[str] = Field(min_length=1)


class ScoringContext(BaseModel):
    job: JobInput
    parsed_job: ParsedJob
    candidate: CandidateProfile
    strategy: Strategy
    evidence_items: list[ScoringEvidenceItem] = Field(default_factory=list)


class ScoreResult(BaseModel):
    total_score: int
    grade: Grade
    eligibility: Eligibility
    hard_rejected: bool
    effective_job_status: EffectiveJobStatus
    action_blockers: list[str]
    dimension_scores: dict[str, Decimal]
    details: list[ScoreDetail]
    rejection_reasons: list[RejectionReason]
    match_reasons: list[str]
    risk_notes: list[str]
    scoring_version: str


DIMENSION_MAX: dict[str, Decimal] = {
    "title": Decimal(15),
    "skills": Decimal(25),
    "experience": Decimal(15),
    "location": Decimal(15),
    "salary": Decimal(15),
    "industry": Decimal(10),
    "management": Decimal(5),
}
