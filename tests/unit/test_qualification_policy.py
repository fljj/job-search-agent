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
        "allowed_onsite_locations": ["济南"],
        "minimum_salary_k": 20,
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
