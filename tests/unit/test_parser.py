from packages.job_parser.models import JobInput, WorkMode
from packages.job_parser.rule_parser import RuleJobParser


def test_parser_extracts_structured_fields() -> None:
    job = JobInput(title="高级Java架构师", company_name="示例公司", work_mode=WorkMode.REMOTE,
                   salary_text="35K-40K·14薪", description="8年以上Java经验，要求Spring Boot和MySQL，Kafka优先。负责架构设计。")
    parsed = RuleJobParser().parse(job)
    assert "Java" in parsed.required_skills
    assert "Kafka" in parsed.preferred_skills
    assert parsed.years_required == 8
    assert parsed.architecture_required is True
    assert parsed.salary is not None
    assert parsed.salary.salary_months == 14


def test_parser_detects_agency_recruiting_from_company_label() -> None:
    job = JobInput(
        title="Java开发工程师",
        company_name="代招公司：上海某大型证券公司",
        description="负责核心业务系统开发。",
    )

    assert RuleJobParser().parse(job).headhunter_detected is True
