import pytest
from pydantic import ValidationError

from packages.job_parser.models import JobInput, ParsedJob, RuleParserConfig, WorkMode
from packages.job_parser.rule_parser import RuleJobParser


def parser() -> RuleJobParser:
    return RuleJobParser(
        RuleParserConfig(
            outsourcing_keywords=["人力外包", "驻场外包", "外包项目"],
            headhunter_keywords=["猎头", "代招"],
            internship_keywords=["实习"],
            full_time_bachelor_keywords=["全日制本科", "统招本科"],
            part_time_keywords=["兼职", "副业"],
            onsite_required_keywords=["要求现场办公", "驻场办公"],
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


def test_only_explicit_full_time_bachelor_requirement_is_detected() -> None:
    explicit = JobInput(
        title="Java开发工程师",
        company_name="示例公司",
        description="要求全日制本科及以上学历。",
    )
    ordinary = explicit.model_copy(
        update={"description": "要求本科及以上学历。"}
    )

    assert parser().parse(explicit).full_time_bachelor_required is True
    assert parser().parse(ordinary).full_time_bachelor_required is False


def test_part_time_and_explicit_onsite_are_detected_separately() -> None:
    flexible = JobInput(
        title="Java开发工程师（兼职）",
        company_name="示例公司",
        description="工作时间可沟通，可以利用晚上或周末开发。",
    )
    onsite = flexible.model_copy(
        update={"description": "兼职合作，但要求现场办公。"}
    )

    flexible_parsed = parser().parse(flexible)
    onsite_parsed = parser().parse(onsite)
    assert flexible_parsed.part_time_detected is True
    assert flexible_parsed.onsite_required_explicitly is False
    assert onsite_parsed.part_time_detected is True
    assert onsite_parsed.onsite_required_explicitly is True


def test_calendar_year_is_not_parsed_as_experience() -> None:
    job = JobInput(
        title="高级后端工程师",
        company_name="示例公司",
        description="项目于2021年启动，要求5年以上后端开发经验。",
    )

    assert parser().parse(job).years_required == 5


def test_calendar_year_without_experience_requirement_is_ignored() -> None:
    job = JobInput(
        title="高级后端工程师",
        company_name="示例公司",
        description="项目于2021年启动，负责核心系统研发。",
    )

    assert parser().parse(job).years_required is None


def test_parsed_job_rejects_calendar_year_as_experience() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 50"):
        ParsedJob(years_required=2021)
