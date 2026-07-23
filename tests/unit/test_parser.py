from packages.job_parser.models import JobInput, RuleParserConfig, WorkMode
from packages.job_parser.rule_parser import RuleJobParser


def parser() -> RuleJobParser:
    return RuleJobParser(
        RuleParserConfig(
            outsourcing_keywords=["人力外包", "驻场外包", "外包项目"],
            headhunter_keywords=["猎头", "代招"],
            internship_keywords=["实习"],
        )
    )


def test_parser_extracts_structured_fields() -> None:
    job = JobInput(title="高级Java架构师", company_name="示例公司", work_mode=WorkMode.REMOTE,
                   salary_text="35K-40K·14薪", description="8年以上Java经验，要求Spring Boot和MySQL，Kafka优先。负责架构设计。")
    parsed = parser().parse(job)
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

    assert parser().parse(job).headhunter_detected is True


def test_hard_filter_keyword_detection_is_configurable() -> None:
    job = JobInput(
        title="Java开发工程师",
        company_name="招聘服务公司",
        description="第三方寻访岗位",
    )

    assert RuleJobParser(RuleParserConfig()).parse(job).headhunter_detected is False
    assert RuleJobParser(
        RuleParserConfig(headhunter_keywords=["第三方寻访"])
    ).parse(job).headhunter_detected is True
