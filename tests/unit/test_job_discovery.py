import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from adapters.browser.job_discovery import (
    BossJobDiscoveryAdapter,
    DiscoveredJob,
    JobDiscoveryBatch,
    JobPrefilterState,
    classify_job_title,
    is_job_list_exhausted,
    is_obviously_irrelevant_title,
    is_potentially_relevant_title,
    next_job_search,
    select_job_candidates,
    verify_job_target,
)
from apps.api.app.core.config import Settings
from apps.api.app.services.automation_service import _proactive_safety_gaps
from apps.api.app.services.job_discovery_service import (
    _is_prewrite_retryable,
    _job_safety_reasons,
    _release_deferred_seen_ids,
    _schedule_retry,
    job_scan_block_reasons,
    mark_retry_target_not_visible,
)
from packages.browser_worker.models import (
    BrowserJob,
    BrowserJobSummary,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)
from packages.policy_engine.automation import AutomationRules


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
        "客户支持专员",
        "客服（Telegram & Discord）",
        "房建技术总工",
        "BD",
        "Mod",
    ],
)
def test_obviously_irrelevant_title_is_filtered_before_detail(title: str) -> None:
    assert is_obviously_irrelevant_title(
        title,
        [
            "施工",
            "推广总监",
            "销售",
            "客户支持",
            "客服",
            "房建",
            "BD",
            "Mod",
        ],
    )


@pytest.mark.parametrize(
    "title",
    [
        "银行系统研发",
        "高级 Java 后端工程师",
        "Vibe Coding工程师",
        "直播运营",
        "Web Developer",
        "结构专业总工（已退休者优先）",
        "风控战略分析师",
    ],
)
def test_broad_or_target_title_still_requires_jd_analysis(title: str) -> None:
    assert not is_obviously_irrelevant_title(
        title,
        ["施工", "推广总监", "销售", "BD", "Mod"],
    )


def test_strategy_direction_overrides_ambiguous_irrelevant_business_word() -> None:
    assert not is_obviously_irrelevant_title(
        "银行风控系统 Java 开发工程师",
        ["风控", "分析师", "销售"],
        ["Java开发"],
    )


def test_target_direction_overrides_customer_service_keyword() -> None:
    assert not is_obviously_irrelevant_title(
        "Java开发（客服系统）",
        ["客服"],
        ["Java开发"],
    )


@pytest.mark.parametrize(
    "title",
    [
        "银行系统研发",
        "AI应用开发工程师（JAVA）",
        "项目研发经理（JAVA方向）",
        "风控系统建设负责人",
        "量化分析师（Python开发方向）",
        "直播运营",
        "Java开发（客服系统）",
    ],
)
def test_target_direction_passes_positive_title_gate(title: str) -> None:
    assert is_potentially_relevant_title(
        title,
        [
            "Java",
            "开发",
            "研发",
            "系统建设",
            "AI",
            "直播运营",
        ],
    )


@pytest.mark.parametrize(
    "title",
    [
        "项目绿化养护区域经理",
        "客户支持专员",
        "房建技术总工",
        "客服（Telegram & Discord）",
        "Retail Manager",
    ],
)
def test_unrelated_direction_is_filtered_by_positive_title_gate(
    title: str,
) -> None:
    assert not is_potentially_relevant_title(
        title,
        ["Java", "开发", "研发", "系统建设", "AI", "直播运营"],
    )


@pytest.mark.parametrize(
    "title",
    [
        "风控系统建设负责人",
        "量化分析师（Python开发方向）",
    ],
)
def test_ambiguous_business_titles_are_not_in_default_irrelevant_config(
    title: str,
) -> None:
    assert not is_obviously_irrelevant_title(
        title,
        [
            "施工",
            "结构专业",
            "推广总监",
            "销售",
            "BD",
            "Mod",
        ],
    )


def test_title_prefilter_keeps_unknown_direction_for_one_detail_read() -> None:
    assert classify_job_title(
        "银行系统研发",
        direction_keywords=["Java"],
        irrelevant_keywords=["施工", "园林", "纯客服"],
        relevant_keywords=[],
    ) is JobPrefilterState.UNKNOWN


@pytest.mark.parametrize("title", ["结构专业总工", "园林养护经理", "纯客服"])
def test_title_prefilter_rejects_only_strong_irrelevant_evidence(title: str) -> None:
    assert classify_job_title(
        title,
        direction_keywords=["Java", "研发"],
        irrelevant_keywords=["结构专业", "园林", "纯客服"],
        relevant_keywords=[],
    ) is JobPrefilterState.IRRELEVANT


def test_virtual_list_exhausts_when_cursor_stops_and_no_new_job_is_visible() -> None:
    assert is_job_list_exhausted(0, "cursor-1", "cursor-1") is True
    assert is_job_list_exhausted(0, "cursor-2", "cursor-1") is False
    assert is_job_list_exhausted(1, "cursor-1", "cursor-1") is False


def test_job_search_rotation_keeps_current_search_until_exhausted() -> None:
    searches = ["推荐", "Java", "区块链工程师"]

    assert next_job_search("Java", searches, exhausted=False) == ("Java", False)


def test_job_search_rotation_switches_tabs_without_refreshing() -> None:
    searches = ["推荐", "Java", "区块链工程师"]

    assert next_job_search("推荐", searches, exhausted=True) == ("Java", False)
    assert next_job_search("Java", searches, exhausted=True) == (
        "区块链工程师",
        False,
    )
    assert next_job_search("区块链工程师", searches, exhausted=True) == (
        "推荐",
        False,
    )


def test_scan_ignores_legacy_refresh_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(BossJobDiscoveryAdapter)
    adapter.config = MagicMock(version="test")
    adapter.selectors = MagicMock(job_list_root="[data-testid='job-list']")
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    listing = MagicMock(
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB_LIST,
        jobs=[],
        cursor=None,
    )
    monkeypatch.setattr(adapter, "_find_list_target", lambda _cdp_url: "ws://page")
    monkeypatch.setattr(
        "adapters.browser.job_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.job_discovery.extract_current_page",
        lambda *_args, **_kwargs: listing,
    )

    batch = adapter.scan(
        "http://127.0.0.1:9222",
        search_key="推荐",
        search_keys=["推荐", "Java", "区块链工程师"],
        refresh_before_scan=True,
    )

    assert batch.refresh_before_next_scan is False
    assert all("location.reload" not in call.args[0] for call in page._evaluate.call_args_list)


def test_scan_waits_for_new_virtual_list_items_after_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(BossJobDiscoveryAdapter)
    adapter.config = MagicMock(version="test")
    adapter.selectors = MagicMock(job_list_root="[data-testid='job-list']")
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    initial = MagicMock(
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB_LIST,
        jobs=[summary(1)],
        cursor=None,
    )
    loaded = MagicMock(
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB_LIST,
        jobs=[summary(2), summary(3)],
        cursor=None,
    )
    monkeypatch.setattr(adapter, "_find_list_target", lambda _cdp_url: "ws://page")
    monkeypatch.setattr(
        "adapters.browser.job_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.job_discovery.extract_current_page",
        MagicMock(side_effect=[initial, loaded]),
    )
    monkeypatch.setattr("adapters.browser.job_discovery.time.sleep", lambda _: None)
    monkeypatch.setattr(
        adapter,
        "_open_detail",
        lambda _cdp_url, _page, item: DiscoveredJob(summary=item),
    )

    batch = adapter.scan(
        "http://127.0.0.1:9222",
        seen_job_ids=["job-1"],
        limit=5,
    )

    assert [item.summary.external_job_id for item in batch.items] == [
        "job-2",
        "job-3",
    ]
    assert batch.exhausted is False


def test_search_activation_waits_for_page_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(BossJobDiscoveryAdapter)
    page = MagicMock()
    page._evaluate.side_effect = [False, False, True]
    sleeps: list[float] = []
    monkeypatch.setattr(
        "adapters.browser.job_discovery.time.sleep",
        sleeps.append,
    )

    adapter._activate_search(page, "推荐")

    assert page._evaluate.call_count == 3
    assert sleeps == [0.25, 0.25]


def test_list_target_detection_waits_for_page_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(BossJobDiscoveryAdapter)
    matches = MagicMock(
        side_effect=[[], [], ["ws://127.0.0.1:9222/devtools/page/job-list"]]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(adapter, "_matching_list_targets", matches)
    monkeypatch.setattr(
        "adapters.browser.job_discovery.time.sleep",
        sleeps.append,
    )

    target = adapter._find_list_target("http://127.0.0.1:9222")

    assert target == "ws://127.0.0.1:9222/devtools/page/job-list"
    assert matches.call_count == 3
    assert sleeps == [0.5, 0.5]


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
        staticmethod(
            lambda cdp_url, target_id, expected_url: closed.append(
                (cdp_url, target_id, expected_url)
            )
        ),
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
            DiscoveredJob(
                summary=summary(1),
                detail_target_id="created-detail",
                detail_target_url="https://www.zhipin.com/job_detail/job-1.html",
            ),
            DiscoveredJob(summary=summary(2)),
        ],
    )

    adapter.close_details("http://127.0.0.1:9222", batch)

    assert closed == [
        (
            "http://127.0.0.1:9222",
            "created-detail",
            "https://www.zhipin.com/job_detail/job-1.html",
        )
    ]


def test_target_close_does_not_close_a_different_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_open(url: str, timeout: int) -> object:
        calls.append(url)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            [
                {
                    "id": "worker-detail",
                    "type": "page",
                    "url": "https://www.zhipin.com/job_detail/job-2.html",
                }
            ]
        ).encode()
        return response

    monkeypatch.setattr(
        "adapters.browser.job_discovery.urlopen",
        fake_open,
    )

    BossJobDiscoveryAdapter._close_target(
        "http://127.0.0.1:9222",
        "worker-detail",
        "https://www.zhipin.com/job_detail/job-1.html",
    )

    assert calls == ["http://127.0.0.1:9222/json/list"]


def test_target_close_closes_the_exact_worker_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_open(url: str, timeout: int) -> object:
        calls.append(url)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            [
                {
                    "id": "worker-detail",
                    "type": "page",
                    "url": "https://www.zhipin.com/job_detail/job-1.html?source=scan",
                }
            ]
        ).encode()
        return response

    monkeypatch.setattr("adapters.browser.job_discovery.urlopen", fake_open)

    BossJobDiscoveryAdapter._close_target(
        "http://127.0.0.1:9222",
        "worker-detail",
        "https://www.zhipin.com/job_detail/job-1.html",
    )

    assert calls == [
        "http://127.0.0.1:9222/json/list",
        "http://127.0.0.1:9222/json/close/worker-detail",
    ]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"source_status": "CLOSED"}, "JOB_NOT_OPEN"),
        ({"external_job_id": None}, "EXTERNAL_JOB_ID_MISSING"),
        ({"company_name": "匿名公司"}, "ANONYMOUS_COMPANY"),
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


def test_unknown_work_mode_does_not_skip_scoring_preflight() -> None:
    job = detail(summary(1)).job
    assert job is not None
    job.work_mode = "UNKNOWN"
    assert "WORK_MODE_UNKNOWN" not in _job_safety_reasons(job)


def test_unknown_work_mode_can_be_confirmed_by_proactive_greeting() -> None:
    job = SimpleNamespace(
        company_name="示例公司",
        work_mode="UNKNOWN",
        external_job_id="job-1",
    )

    assert _proactive_safety_gaps(job, "招聘人") == []


def test_proactive_contact_allows_salary_to_be_confirmed_later() -> None:
    job = detail(summary(1)).job
    assert job is not None
    job.salary_text = None

    assert "SALARY_UNKNOWN" not in _job_safety_reasons(job)


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


def test_single_job_retry_does_not_block_other_job_scans() -> None:
    rules = AutomationRules(
        enabled=True,
        job_scan_enabled=True,
        work_start_hour=0,
        work_end_hour=24,
    )
    run = SimpleNamespace(cursor={})

    assert job_scan_block_reasons(  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        run,  # type: ignore[arg-type]
        rules,
        datetime.now(UTC),
    ) == []


def test_batch_backoff_releases_current_and_unprocessed_job_ids() -> None:
    now = datetime.now(UTC)
    batch = JobDiscoveryBatch(
        platform=Platform.BOSS,
        search_key="Java",
        scroll_position=3,
        scanned_at=now,
        next_scan_at=now,
        items=[
            DiscoveredJob(summary=summary(1)),
            DiscoveredJob(summary=summary(2)),
            DiscoveredJob(summary=summary(3)),
        ],
        seen_job_ids=["older-job", "job-1", "job-2", "job-3"],
    )

    _release_deferred_seen_ids(batch, 1)

    assert batch.seen_job_ids == ["older-job", "job-1"]


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


def test_missing_retry_target_advances_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        boss_llm_retry_base_seconds=300,
        boss_job_retry_max_attempts=3,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.get_settings",
        lambda: settings,
    )
    record = SimpleNamespace(
        retry_count=0,
        status="RETRYABLE",
        reason_codes=["LLM_TIMEOUT"],
        next_retry_at=None,
    )
    session = SimpleNamespace(commit=lambda: None)
    now = datetime.now(UTC)

    mark_retry_target_not_visible(  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
        record,  # type: ignore[arg-type]
        now=now,
    )

    assert record.retry_count == 1
    assert record.reason_codes == ["JOB_RETRY_TARGET_NOT_VISIBLE"]
    assert record.next_retry_at == now + timedelta(seconds=300)
