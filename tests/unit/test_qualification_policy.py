import pytest

from packages.policy_engine.qualification import (
    QualificationContext,
    QualificationStatus,
    evaluate_qualification,
)


def context(**changes: object) -> QualificationContext:
    values: dict[str, object] = {
        "company_name": "示例科技",
        "job_title": "Java后端开发",
        "industry": "互联网",
        "location": "远程",
        "work_mode": "REMOTE",
        "salary_text": "25K-35K",
        "description": "负责 Java 服务端系统研发",
        "accepted_directions": ["Java后端", "Vibe Coding", "直播运营"],
        "excluded_industries": ["培训"],
        "blacklisted_companies": ["黑名单公司"],
        "enabled_work_modes": ["REMOTE", "ONSITE"],
        "allowed_locations": ["济南"],
        "salary_threshold_k": 20,
        "prohibited_direction_keywords": ["保险销售", "保险增员", "刷单"],
        "related_direction_keywords": [
            "Java", "后端", "开发", "Vibe Coding", "直播运营"
        ],
    }
    values.update(changes)
    return QualificationContext.model_validate(values)


def test_complete_related_job_is_full_match() -> None:
    assert evaluate_qualification(context())[0] is QualificationStatus.FULL_MATCH


def test_partial_related_job_is_rough_match() -> None:
    status, _ = evaluate_qualification(
        context(location=None, salary_text=None, description=None)
    )
    assert status is QualificationStatus.ROUGH_MATCH


def test_unknown_work_mode_still_applies_salary_threshold() -> None:
    status, evidence = evaluate_qualification(
        context(
            work_mode="UNKNOWN",
            salary_text="9-10K",
            salary_threshold_k=15,
        )
    )

    assert status is QualificationStatus.MISMATCH
    assert evidence == ["SALARY_CONFLICT"]


def test_unknown_job_stays_unknown() -> None:
    status, _ = evaluate_qualification(
        QualificationContext(message_text="您好，在看机会吗？")
    )
    assert status is QualificationStatus.UNKNOWN


@pytest.mark.parametrize(
    "changes",
    [
        {"message_text": "保险销售，主要负责增员"},
        {"company_name": "黑名单公司"},
        {"industry": "培训"},
        {"work_mode": "HYBRID"},
        {"work_mode": "ONSITE", "location": "北京"},
        {"job_title": "线下销售"},
        {"salary_text": "10K-15K"},
    ],
)
def test_known_conflicts_are_mismatch(changes: dict[str, object]) -> None:
    assert (
        evaluate_qualification(context(**changes))[0]
        is QualificationStatus.MISMATCH
    )


def test_company_blacklist_uses_normalized_exact_match() -> None:
    assert (
        evaluate_qualification(
            context(
                company_name="黑名单公司技术中心",
                blacklisted_companies=["黑名单公司"],
            )
        )[0]
        is QualificationStatus.FULL_MATCH
    )


def test_new_message_can_correct_stale_job_direction() -> None:
    status, _ = evaluate_qualification(
        context(
            job_title="产品经理",
            message_text="现在联系的是 Java后端开发岗位",
        )
    )
    assert status is QualificationStatus.ROUGH_MATCH


def test_java_technology_evidence_matches_nonstandard_job_title() -> None:
    status, evidence = evaluate_qualification(
        context(
            job_title="AI应用开发工程师（JAVA）",
            description="负责 AI 应用开发，使用 SpringBoot、Spring Cloud 和 JVM",
        )
    )

    assert status is QualificationStatus.FULL_MATCH
    assert evidence == ["FULL_JOB_CONTEXT_AVAILABLE"]


def test_java_in_description_matches_generic_system_development_title() -> None:
    status, _ = evaluate_qualification(
        context(
            job_title="银行系统开发",
            description="负责银行系统的 Java 服务端与 SpringBoot 微服务开发",
        )
    )

    assert status is QualificationStatus.FULL_MATCH


def test_java_does_not_match_javascript_direction() -> None:
    status, evidence = evaluate_qualification(
        context(
            job_title="JavaScript前端工程师",
            description="负责 React 前端开发",
        )
    )

    assert status is QualificationStatus.MISMATCH
    assert evidence == ["JOB_DIRECTION_CONFLICT"]


def test_hybrid_location_and_yuan_salary_are_checked() -> None:
    assert (
        evaluate_qualification(
            context(work_mode="HYBRID", enabled_work_modes=["HYBRID"], location="北京")
        )[0]
        is QualificationStatus.MISMATCH
    )
    assert (
        evaluate_qualification(context(salary_text="10000-15000元/月"))[0]
        is QualificationStatus.MISMATCH
    )
