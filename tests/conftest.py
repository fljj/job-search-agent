from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.job_parser.models import (
    JobInput,
    ParsedJob,
    SalaryRange,
    SeniorityLevel,
    SourceJobStatus,
    WorkMode,
)
from packages.job_matching.models import (
    CandidateProfile,
    CandidateSkill,
    IndustryRule,
    IndustryRuleType,
    RuleType,
    SalaryRule,
    JobDecisionContext,
    Strategy,
    TitleRule,
    WorkModeRule,
)


@pytest.fixture
def candidate() -> CandidateProfile:
    return CandidateProfile(
        name="测试候选人", total_years=Decimal(13), management_years=Decimal(5),
        has_architecture_experience=True, has_core_system_experience=True,
        skills=[CandidateSkill(name=name, source="resume", is_core=name == "Java")
                for name in ("Java", "Spring Boot", "MySQL", "Redis", "Kafka", "Docker")],
        industry_experiences=["互联网", "金融科技"],
    )


@pytest.fixture
def strategy() -> Strategy:
    return Strategy(
        name="Java 后端", title_rules=[
            TitleRule(rule_type=RuleType.INCLUDE, pattern="Java后端"),
            TitleRule(rule_type=RuleType.EXCLUDE, pattern="Android"),
        ],
        accepted_seniority_levels=[SeniorityLevel.MIDDLE, SeniorityLevel.SENIOR,
                                   SeniorityLevel.LEAD, SeniorityLevel.ARCHITECT],
        work_mode_rules=[
            WorkModeRule(work_mode=WorkMode.REMOTE, enabled=True),
            WorkModeRule(work_mode=WorkMode.ONSITE, enabled=True, location_restricted=True,
                         allowed_locations=["济南"]),
            WorkModeRule(work_mode=WorkMode.HYBRID, enabled=True, location_restricted=True,
                         allowed_locations=["济南"]),
            WorkModeRule(work_mode=WorkMode.UNKNOWN, enabled=True),
        ],
        salary_rules=[
            SalaryRule(work_mode=WorkMode.REMOTE, minimum_monthly_k=Decimal(35),
                       expected_monthly_k=Decimal(40)),
            SalaryRule(work_mode=WorkMode.ONSITE, minimum_monthly_k=Decimal(13),
                       expected_monthly_k=Decimal(15)),
        ],
        industry_rules=[
            IndustryRule(industry="互联网", rule_type=IndustryRuleType.PREFERRED),
            IndustryRule(industry="培训", rule_type=IndustryRuleType.EXCLUDED),
        ],
        company_blacklist=["黑名单公司"], core_required_skills=["Java"],
        accept_outsourcing=False, accept_headhunter=False, max_posted_days=30,
    )


@pytest.fixture
def job() -> JobInput:
    return JobInput(
        title="Java后端工程师", company_name="示例科技", industry="互联网", location="北京",
        work_mode=WorkMode.REMOTE, salary_text="40K-45K", description="5年以上Java和Spring Boot经验",
        published_at=datetime.now(UTC), source_status=SourceJobStatus.OPEN,
    )


@pytest.fixture
def parsed() -> ParsedJob:
    return ParsedJob(
        required_skills=["Java", "Spring Boot", "MySQL"], preferred_skills=["Kafka"],
        years_required=Decimal(5), seniority_level=SeniorityLevel.SENIOR,
        salary=SalaryRange(minimum_monthly_k=Decimal(40), maximum_monthly_k=Decimal(45),
                           salary_months=12, is_pre_tax=True),
    )


@pytest.fixture
def context(job: JobInput, parsed: ParsedJob, candidate: CandidateProfile,
            strategy: Strategy) -> JobDecisionContext:
    return JobDecisionContext(job=job, parsed_job=parsed, candidate=candidate, strategy=strategy)
