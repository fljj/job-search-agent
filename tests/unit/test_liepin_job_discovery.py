import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from adapters.browser.job_discovery import DiscoveredJob, JobDiscoveryBatch
from adapters.browser.liepin_job_discovery import LiepinJobDiscoveryAdapter
from adapters.browser.read_only_actions import ReadOnlyActionExecutor
from apps.api.app.models import entities as db
from apps.api.app.services.job_discovery_service import process_job_discovery_batch
from apps.api.app.services.message_discovery_service import _page_role
from packages.browser_worker.actions import ApprovedCommand, ExecutionOutcome
from packages.browser_worker.models import (
    BrowserJob,
    BrowserJobSummary,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)
from packages.policy_engine.automation import AutomationRules


def _summary(external_id: str, title: str) -> BrowserJobSummary:
    return BrowserJobSummary(
        external_job_id=external_id,
        title=title,
        company_name="示例科技",
        detail_url=f"https://www.liepin.com/job/{external_id}.shtml",
    )


def _detail(item: BrowserJobSummary, *, recruiter_role: str = "DIRECT_EMPLOYER") -> ReadResult:
    return ReadResult(
        platform=Platform.LIEPIN,
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
            recruiter_name="李女士",
            recruiter_role=recruiter_role,
            description="负责 Java 服务端研发",
            source_status="OPEN",
        ),
    )


def test_scan_prefilters_irrelevant_title_before_opening_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(LiepinJobDiscoveryAdapter)
    adapter.selectors = MagicMock(version="liepin-v1")
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    jobs = [
        _summary("irrelevant", "施工项目经理"),
        _summary("relevant", "Java 后端工程师"),
        _summary("unknown", "银行系统负责人"),
    ]
    listing = ReadResult(
        platform=Platform.LIEPIN,
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB_LIST,
        page_url="https://c.liepin.com/",
        page_title="猎聘首页",
        content_hash="b" * 64,
        selector_version="liepin-v1",
        jobs=jobs,
    )
    opened: list[str] = []
    monkeypatch.setattr(adapter, "_find_home_target", lambda _url: "ws://home")
    monkeypatch.setattr(
        "adapters.browser.liepin_job_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.liepin_job_discovery.extract_current_page",
        lambda *_args, **_kwargs: listing,
    )
    monkeypatch.setattr(adapter, "_scroll_home", lambda _page: None)
    monkeypatch.setattr(
        adapter,
        "_open_detail",
        lambda _cdp, _page, item: opened.append(item.external_job_id)
        or DiscoveredJob(summary=item, detail=_detail(item)),
    )

    batch = adapter.scan(
        "http://127.0.0.1:9222",
        irrelevant_title_keywords=["施工"],
        direction_title_keywords=["Java", "研发"],
        limit=3,
    )

    assert opened == []
    assert batch.items[0].reason_codes == ["TITLE_STRONGLY_IRRELEVANT"]
    assert batch.seen_job_ids == ["irrelevant"]
    assert len(batch.items) == 1
    assert batch.platform is Platform.LIEPIN
    assert batch.refresh_before_next_scan is False


def test_close_only_exact_worker_owned_liepin_detail(
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
                    "url": "https://www.liepin.com/job/1983664515.shtml?track=mock",
                }
            ]
        ).encode()
        return response

    monkeypatch.setattr(
        "adapters.browser.liepin_job_discovery.urlopen", fake_open
    )

    LiepinJobDiscoveryAdapter._close_target(
        "http://127.0.0.1:9222",
        "worker-detail",
        "https://www.liepin.com/job/1983664515.shtml",
    )

    assert calls == [
        "http://127.0.0.1:9222/json/list",
        "http://127.0.0.1:9222/json/close/worker-detail",
    ]


@pytest.mark.parametrize(
    (
        "recruiter_role",
        "execute_external_actions",
        "expected_reasons",
        "expected_contacted",
    ),
    [
        ("DIRECT_EMPLOYER", False, ["PROACTIVE_CONTACT_CANDIDATE"], 0),
        ("DIRECT_EMPLOYER", True, ["GREETING_SENT"], 1),
        (
            "HEADHUNTER",
            False,
            [
                "PROACTIVE_CONTACT_NOT_ELIGIBLE",
                "HEADHUNTER_PROACTIVE_CONTACT_BLOCKED",
            ],
            0,
        ),
        (
            "HEADHUNTER",
            True,
            ["HEADHUNTER_PROACTIVE_CONTACT_BLOCKED"],
            0,
        ),
    ],
)
def test_liepin_batch_never_contacts_headhunter_or_writes_in_read_only_mode(
    monkeypatch: pytest.MonkeyPatch,
    recruiter_role: str,
    execute_external_actions: bool,
    expected_reasons: list[str],
    expected_contacted: int,
) -> None:
    item = _summary("liepin-job-1", "Java 后端工程师")
    batch = JobDiscoveryBatch(
        platform=Platform.LIEPIN,
        search_key="HOME",
        scroll_position=1,
        scanned_at=datetime.now(UTC),
        next_scan_at=datetime.now(UTC),
        seen_job_ids=[item.external_job_id],
        items=[
            DiscoveredJob(
                summary=item,
                detail=_detail(item, recruiter_role=recruiter_role),
            )
        ],
    )
    run = db.AgentRun(
        id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        platform="LIEPIN",
        cursor={},
    )
    record = SimpleNamespace(
        status="DISCOVERED",
        reason_codes=[],
        next_retry_at=None,
        company_name=item.company_name,
        job_title=item.title,
        recruiter_name=None,
        prefilter_state="UNKNOWN",
        prefilter_reason=None,
        job_id=None,
        job_score_id=None,
        content_hash=None,
    )
    job_id = uuid4()
    job = SimpleNamespace(
        id=job_id,
        company_name=item.company_name,
        title=item.title,
        description="负责 Java 服务端研发",
        content_hash="c" * 64,
    )
    strategy = SimpleNamespace(candidate_profile_id=uuid4())
    score = SimpleNamespace(
        id=uuid4(),
        hard_rejected=False,
        rejection_reasons=[],
        automation_eligible=True,
        action_blockers=[],
    )
    session = MagicMock()
    session.get.side_effect = lambda model, _id: (
        strategy if model is db.JobStrategy else job
    )
    greet = MagicMock()
    dispatch = MagicMock(
        return_value={
            "action_id": uuid4(),
            "action_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service._effective_rules",
        lambda *_args: AutomationRules(
            enabled=True,
            auto_greet_enabled=True,
            job_scan_enabled=True,
            work_start_hour=0,
            work_end_hour=24,
        ),
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.job_scan_block_reasons",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service._record_for_item",
        lambda *_args: record,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.import_job",
        lambda *_args, **_kwargs: SimpleNamespace(job=SimpleNamespace(id=job_id)),
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service._duplicate_reason",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service._cooldown_reason",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.create_score",
        lambda *_args, **_kwargs: score,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.create_greeting_draft", greet
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service.dispatch_proactive_greeting",
        dispatch,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service._state_event",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "apps.api.app.services.job_discovery_service._event",
        lambda *_args: None,
    )

    counts = process_job_discovery_batch(
        session,
        run,
        batch,
        provider=MagicMock(),
        executor=ReadOnlyActionExecutor(),
        cdp_url="http://127.0.0.1:9222",
        execute_external_actions=execute_external_actions,
    )

    assert counts == {
        "discovered": 1,
        "scored": 1,
        "contacted": expected_contacted,
        "skipped": 0,
    }
    assert record.status == ("CONTACTED" if expected_contacted else "SCORED")
    assert record.reason_codes == expected_reasons
    if expected_contacted:
        greet.assert_called_once()
        dispatch.assert_called_once()
    else:
        greet.assert_not_called()
        dispatch.assert_not_called()


def test_read_only_executor_fails_closed_before_write() -> None:
    result = ReadOnlyActionExecutor().execute(
        "http://127.0.0.1:9222",
        ApprovedCommand(
            action_type="GREETING",
            platform="LIEPIN",
            external_job_id="liepin-job-1",
            company="示例科技",
            job_title="Java 后端工程师",
            recruiter="李女士",
        ),
    )

    assert result.outcome is ExecutionOutcome.FAILED_FINAL
    assert result.error_code == "PLATFORM_WRITES_NOT_ENABLED"
    assert result.write_started is False


def test_only_liepin_candidate_home_is_registered_as_job_list() -> None:
    assert _page_role("LIEPIN", "https://c.liepin.com/?time=1") == "JOB_LIST"
    assert _page_role("LIEPIN", "https://www.liepin.com/") is None
    assert _page_role("LIEPIN", "https://example.com/") is None
