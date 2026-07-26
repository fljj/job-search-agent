import pytest

from adapters.browser.job_discovery import select_job_candidates, verify_job_target
from apps.api.app.services.job_discovery_service import _job_safety_reasons
from packages.browser_worker.models import (
    BrowserJob,
    BrowserJobSummary,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


def summary(index: int) -> BrowserJobSummary:
    return BrowserJobSummary(
        external_job_id=f"job-{index}",
        title="Java 后端工程师",
        company_name=f"公司-{index}",
        detail_url=f"https://www.zhipin.com/job_detail/job-{index}.html",
    )


def detail(item: BrowserJobSummary) -> ReadResult:
    return ReadResult(
        platform=Platform.BOSS,
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB,
        page_url=item.detail_url or "",
        page_title=item.title,
        content_hash="a" * 64,
        selector_version="fixture",
        job=BrowserJob(
            external_job_id=item.external_job_id,
            title=item.title,
            company_name=item.company_name,
            location="济南",
            work_mode="ONSITE",
            salary_text="20K-30K",
            recruiter_name="招聘人",
            description="Java 后端开发",
            source_status="OPEN",
        ),
    )


def test_scans_500_jobs_idempotently_after_list_reorder() -> None:
    items = [summary(index) for index in range(500)]
    seen: list[str] = []
    selected: list[str] = []
    for position in range(0, 500, 25):
        batch = select_job_candidates(
            items, seen, scroll_position=position, limit=25
        )
        selected.extend(item.external_job_id for item in batch)
        seen.extend(item.external_job_id for item in batch)
    assert len(selected) == 500
    assert len(set(selected)) == 500
    assert (
        select_job_candidates(
            list(reversed(items)), seen, scroll_position=0, limit=500
        )
        == []
    )


def test_job_detail_verification_rejects_switched_job() -> None:
    selected = summary(1)
    assert verify_job_target(selected, detail(selected)) == []
    switched = detail(selected)
    assert switched.job is not None
    switched.job.external_job_id = "job-other"
    assert verify_job_target(selected, switched) == ["JOB_ID_MISMATCH"]


def test_job_detail_accepts_full_company_name_for_truncated_list_name() -> None:
    selected = summary(1)
    result = detail(selected)
    assert result.job is not None
    result.job.company_name = f"{selected.company_name}有限责任公司"

    assert verify_job_target(selected, result) == []

    result.job.company_name = "完全不同的公司"
    assert verify_job_target(selected, result) == ["JOB_COMPANY_MISMATCH"]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"source_status": "CLOSED"}, "JOB_NOT_OPEN"),
        ({"external_job_id": None}, "EXTERNAL_JOB_ID_MISSING"),
        ({"company_name": "匿名公司"}, "ANONYMOUS_COMPANY"),
        ({"work_mode": "UNKNOWN"}, "WORK_MODE_UNKNOWN"),
        ({"salary_text": None}, "SALARY_UNKNOWN"),
        ({"recruiter_name": None}, "RECRUITER_UNKNOWN"),
    ],
)
def test_proactive_contact_requires_complete_safe_job(
    changes: dict[str, object], reason: str
) -> None:
    job = detail(summary(1)).job
    assert job is not None
    for field, value in changes.items():
        setattr(job, field, value)
    assert reason in _job_safety_reasons(job)
