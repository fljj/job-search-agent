from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from adapters.browser.job_discovery import (
    BossJobDiscoveryAdapter,
    DiscoveredJob,
    JobDiscoveryBatch,
    is_job_list_exhausted,
    is_obviously_irrelevant_title,
    next_job_search,
    select_job_candidates,
    verify_job_target,
)
from apps.api.app.core.config import Settings
from apps.api.app.services.job_discovery_service import (
    _is_prewrite_retryable,
    _job_safety_reasons,
    _schedule_retry,
)
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


def test_virtual_list_selects_unseen_items_without_reusing_cumulative_offset() -> None:
    first_view = [summary(index) for index in range(10)]
    second_view = [summary(index) for index in range(10, 20)]
    seen = [item.external_job_id for item in first_view]

    selected = select_job_candidates(
        second_view,
        seen,
        scroll_position=0,
        limit=10,
    )

    assert [item.external_job_id for item in selected] == [
        f"job-{index}" for index in range(10, 20)
    ]


def test_seen_items_do_not_hide_later_unseen_jobs_in_same_list() -> None:
    items = [summary(index) for index in range(20)]

    selected = select_job_candidates(
        items,
        [f"job-{index}" for index in range(10)],
        scroll_position=0,
        limit=5,
    )

    assert [item.external_job_id for item in selected] == [
        f"job-{index}" for index in range(10, 15)
    ]


@pytest.mark.parametrize(
    "title",
    [
        "施工项目经理",
        "科技服务推广总监",
        "高级销售经理",
        "BD",
        "Mod",
        "风控战略分析师",
    ],
)
def test_obviously_irrelevant_title_is_filtered_before_detail(title: str) -> None:
    assert is_obviously_irrelevant_title(
        title,
        ["施工", "推广总监", "销售", "BD", "Mod", "风控"],
    )


@pytest.mark.parametrize(
    "title",
    [
        "银行系统研发",
        "高级 Java 后端工程师",
        "Vibe Coding工程师",
        "直播运营",
        "Web Developer",
    ],
)
def test_broad_or_target_title_still_requires_jd_analysis(title: str) -> None:
    assert not is_obviously_irrelevant_title(
        title,
        ["施工", "推广总监", "销售", "BD", "Mod", "风控"],
    )


def test_virtual_list_exhausts_when_cursor_stops_and_no_new_job_is_visible() -> None:
    assert is_job_list_exhausted(0, "cursor-1", "cursor-1") is True
    assert is_job_list_exhausted(0, "cursor-2", "cursor-1") is False
    assert is_job_list_exhausted(1, "cursor-1", "cursor-1") is False


def test_job_search_rotation_keeps_current_search_until_exhausted() -> None:
    searches = ["推荐", "Java", "区块链工程师"]

    assert next_job_search("Java", searches, exhausted=False) == ("Java", False)


def test_job_search_rotation_switches_tabs_and_refreshes_after_full_cycle() -> None:
    searches = ["推荐", "Java", "区块链工程师"]

    assert next_job_search("推荐", searches, exhausted=True) == ("Java", False)
    assert next_job_search("Java", searches, exhausted=True) == (
        "区块链工程师",
        False,
    )
    assert next_job_search("区块链工程师", searches, exhausted=True) == (
        "推荐",
        True,
    )


def test_unknown_job_search_recovers_to_first_configured_search() -> None:
    assert next_job_search(
        "已删除的入口", ["推荐", "Java"], exhausted=True
    ) == ("推荐", False)


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


def test_closes_only_detail_targets_created_for_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        BossJobDiscoveryAdapter,
        "_close_target",
        staticmethod(lambda cdp_url, target_id: closed.append((cdp_url, target_id))),
    )
    adapter = object.__new__(BossJobDiscoveryAdapter)
    now = datetime.now(UTC)
    batch = JobDiscoveryBatch(
        platform=Platform.BOSS,
        search_key="Java",
        scroll_position=0,
        scanned_at=now,
        next_scan_at=now,
        items=[
            DiscoveredJob(summary=summary(1), detail_target_id="created-detail"),
            DiscoveredJob(summary=summary(2)),
        ],
    )

    adapter.close_details("http://127.0.0.1:9222", batch)

    assert closed == [("http://127.0.0.1:9222", "created-detail")]


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


def test_retry_uses_exponential_backoff_and_stops_after_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        boss_llm_retry_base_seconds=300,
        boss_llm_retry_max_seconds=3600,
        boss_job_retry_max_attempts=3,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.get_settings",
        lambda: settings,
    )
    record = SimpleNamespace(
        retry_count=0,
        status="DISCOVERED",
        reason_codes=[],
        next_retry_at=None,
    )
    now = datetime.now(UTC)

    first = _schedule_retry(record, "LLM_RATE_LIMITED", now)
    assert first == now + timedelta(seconds=300)
    second = _schedule_retry(record, "LLM_RATE_LIMITED", now)
    assert second == now + timedelta(seconds=600)
    exhausted = _schedule_retry(record, "LLM_RATE_LIMITED", now)
    assert exhausted is None
    assert record.status == "SKIPPED"
    assert record.reason_codes == [
        "LLM_RATE_LIMITED",
        "RETRY_ATTEMPTS_EXHAUSTED",
    ]


def test_only_prewrite_failure_allows_greeting_retry() -> None:
    retryable = SimpleNamespace(
        status="FAILED_RETRYABLE",
        failure_code="APPROVED_TARGET_PAGE_NOT_FOUND",
    )
    unknown = SimpleNamespace(
        status="OUTCOME_UNKNOWN",
        failure_code="RESULT_NOT_OBSERVED",
    )

    assert _is_prewrite_retryable(retryable)  # type: ignore[arg-type]
    assert not _is_prewrite_retryable(unknown)  # type: ignore[arg-type]
