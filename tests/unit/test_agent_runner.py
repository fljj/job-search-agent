import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.job_discovery import DiscoveredJob, JobDiscoveryBatch
from adapters.browser.message_discovery import (
    BossMessageDiscoveryAdapter,
    MessageDiscoveryAdapter,
    MessageDiscoveryBatch,
)
from adapters.browser.playwright_actions import PlaywrightActionExecutor
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.config import Settings
from apps.api.app.models import entities as db
from packages.browser_worker.actions import (
    ApprovedCommand,
    ExecutionOutcome,
    ExecutionResult,
)
from packages.browser_worker.models import BrowserJobSummary, Platform
from packages.policy_engine.automation import AutomationRules
from scripts.run_agent_worker import (
    _build_executor,
    _discover_messages,
    _heartbeat_loop,
    _merge_seen_job_ids,
    _process_maimai_recommendations,
    _run_boss_job_discovery,
    _single_worker_lock,
)


def command() -> ApprovedCommand:
    return ApprovedCommand(
        action_type="REPLY",
        platform="MOCK",
        conversation_key="conversation-1",
        company="测试公司",
        job_title="Java后端",
        recruiter="招聘人",
        content="您好",
    )


def test_persisted_discovery_records_are_always_treated_as_seen() -> None:
    assert _merge_seen_job_ids(
        ["cursor-job", "repeated-job"],
        ["repeated-job", "retryable-job"],
    ) == ["cursor-job", "repeated-job", "retryable-job"]


def test_retryable_discovery_records_are_removed_from_seen_cursor() -> None:
    assert _merge_seen_job_ids(
        ["cursor-job", "retryable-job"],
        ["persisted-job"],
        ["retryable-job"],
    ) == ["cursor-job", "persisted-job"]


def test_boss_job_search_labels_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        boss_job_search_labels="推荐, Java ,区块链工程师,,",
    )

    assert settings.boss_job_searches == ["推荐", "Java", "区块链工程师"]


def test_invisible_retry_target_falls_back_to_normal_job_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = db.AgentRun(
        id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        platform="BOSS",
        cursor={"job_discovery": {"search_key": "推荐"}},
    )
    retry = MagicMock(external_job_id="retry-job")
    now = datetime.now(UTC)
    empty_retry = JobDiscoveryBatch(
        platform=Platform.BOSS,
        search_key="推荐",
        scroll_position=0,
        scanned_at=now,
        next_scan_at=now,
        exhausted=True,
    )
    normal = JobDiscoveryBatch(
        platform=Platform.BOSS,
        search_key="推荐",
        scroll_position=2,
        scanned_at=now,
        next_scan_at=now,
        items=[
            DiscoveredJob(summary=BrowserJobSummary(
                external_job_id="job-1", title="Java", company_name="公司一"
            )),
            DiscoveredJob(summary=BrowserJobSummary(
                external_job_id="job-2", title="Java", company_name="公司二"
            )),
        ],
    )
    adapter = MagicMock()
    adapter.scan.side_effect = [empty_retry, normal]
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = []
    session.get.return_value = MagicMock(title_rules=[])
    marked: list[object] = []
    processed: list[JobDiscoveryBatch] = []
    monkeypatch.setattr(
        "scripts.run_agent_worker.BossJobDiscoveryAdapter",
        lambda _config: adapter,
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.next_retryable_job", lambda *_: retry
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.mark_retry_target_not_visible",
        lambda _session, record: marked.append(record),
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.process_job_discovery_batch",
        lambda _session, _run, batch, **_kwargs: processed.append(batch),
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.get_settings",
        lambda: Settings(_env_file=None, boss_job_batch_size=5),
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.get_job_parser_config", MagicMock()
    )
    monkeypatch.setattr("scripts.run_agent_worker.get_browser_selectors", MagicMock())
    monkeypatch.setattr("scripts.run_agent_worker.build_runtime_llm_provider", MagicMock())
    monkeypatch.setattr("scripts.run_agent_worker.runtime_event", lambda *_, **__: None)

    _run_boss_job_discovery(
        session,
        run,
        "worker-1",
        "http://127.0.0.1:9222",
        MagicMock(),
        AutomationRules(),
    )

    assert marked == [retry]
    assert adapter.scan.call_count == 2
    assert adapter.scan.call_args_list[0].kwargs["target_job_ids"] == {"retry-job"}
    assert adapter.scan.call_args_list[0].kwargs["limit"] == 1
    assert adapter.scan.call_args_list[1].kwargs["target_job_ids"] is None
    assert adapter.scan.call_args_list[1].kwargs["limit"] == 5
    assert processed == [normal]


def test_fake_executor_is_offline_and_records_commands() -> None:
    executor = FakeActionExecutor()
    result = executor.execute("http://127.0.0.1:9222", command())
    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert executor.commands == [command()]


def test_fake_executor_can_simulate_safety_failure() -> None:
    executor = FakeActionExecutor(
        [
            ExecutionResult(
                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                error_code="RESULT_NOT_OBSERVED",
            )
        ]
    )
    result = executor.execute("http://127.0.0.1:9222", command())
    assert result.outcome is ExecutionOutcome.OUTCOME_UNKNOWN


def test_worker_executor_mode_is_explicitly_isolated() -> None:
    real_executor, real_type = _build_executor("BOSS", "REAL")
    fake_executor, fake_type = _build_executor("MOCK", "FAKE")

    assert isinstance(real_executor, PlaywrightActionExecutor)
    assert real_type == "REAL_CDP"
    assert isinstance(fake_executor, FakeActionExecutor)
    assert fake_type == "FAKE"
    with pytest.raises(ValueError, match="禁止使用 Fake"):
        _build_executor("BOSS", "FAKE")
    with pytest.raises(ValueError, match="显式配置 Fake"):
        _build_executor("MOCK", "REAL")


def test_only_one_worker_can_hold_process_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "scripts.run_agent_worker.LOCK_PATH",
        f"{tmp_path}/agent-worker.lock",
    )
    with _single_worker_lock(), pytest.raises(RuntimeError, match="已有 Agent Worker"):
        with _single_worker_lock():
            pytest.fail("第二个 Worker 不应取得进程锁")


def test_worker_heartbeat_continues_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intervals: list[int] = []
    heartbeats: list[str] = []

    class StopAfterOneHeartbeat:
        def wait(self, interval: int) -> bool:
            intervals.append(interval)
            return len(intervals) > 1

    monkeypatch.setattr(
        "scripts.run_agent_worker._send_worker_heartbeat",
        heartbeats.append,
    )

    _heartbeat_loop(
        "worker-1",
        20,
        cast(threading.Event, StopAfterOneHeartbeat()),
    )

    assert intervals == [20, 20]
    assert heartbeats == ["worker-1"]


def test_message_discovery_reuses_platform_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    run = db.AgentRun(
        id=uuid4(),
        platform="MAIMAI",
        cursor={
            "message_discovery": {
                "partition": "UNREAD",
                "scroll_position": 20,
                "seen_message_keys": ["chat-1:message-1"],
            },
            "message_discovery_health": {
                "consecutive_failure_count": 2,
            },
        },
    )
    batch = MessageDiscoveryBatch(
        platform=Platform.MAIMAI,
        partition="ALL",
        scroll_position=30,
        scanned_at=datetime.now(UTC),
    )
    adapter = MagicMock(spec=MessageDiscoveryAdapter)
    adapter.scan.return_value = batch
    session.scalars.return_value.all.return_value = ["closed-chat"]
    persisted: list[MessageDiscoveryBatch] = []

    def persist(
        _session: Session,
        _run: db.AgentRun,
        _worker: str,
        value: MessageDiscoveryBatch,
    ) -> dict[str, int]:
        persisted.append(value)
        return {"imported": 0, "paused": 0}

    monkeypatch.setattr(
        "scripts.run_agent_worker.record_ready_platform_session",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.get_settings",
        lambda: MagicMock(agent_tick_batch_size=10),
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.persist_discovery_batch",
        persist,
    )
    monkeypatch.setattr("scripts.run_agent_worker.runtime_event", lambda *_, **__: None)

    assert _discover_messages(
        session, run, "worker-1", "http://127.0.0.1:9222", adapter
    )
    adapter.scan.assert_called_once_with(
        "http://127.0.0.1:9222",
        partition="ALL",
        scroll_position=20,
        seen_message_keys=["chat-1:message-1"],
            excluded_conversation_ids=["closed-chat"],
            terminal_message_ids={},
        known_linked_job_ids={},
        limit=10,
    )
    assert persisted == [batch]
    assert "message_discovery_health" not in (run.cursor or {})


def test_message_discovery_pauses_only_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    maimai_run = db.AgentRun(id=uuid4(), platform="MAIMAI", cursor={})
    boss_run = db.AgentRun(id=uuid4(), platform="BOSS", cursor={})
    adapter = MagicMock(spec=MessageDiscoveryAdapter)
    adapter.scan.side_effect = ValueError("页面变化")
    paused: list[object] = []
    monkeypatch.setattr(
        "scripts.run_agent_worker.pause_run",
        lambda _session, run_id, _reasons: paused.append(run_id),
    )

    for _ in range(2):
        assert not _discover_messages(
            session,
            maimai_run,
            "worker-1",
            "http://127.0.0.1:9222",
            adapter,
        )
        assert paused == []
    assert not _discover_messages(
        session, maimai_run, "worker-1", "http://127.0.0.1:9222", adapter
    )
    assert paused == [maimai_run.id]
    assert boss_run.id not in paused


def test_boss_missing_message_page_is_reopened_without_pausing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = []
    session.execute.return_value.all.return_value = []
    run = db.AgentRun(id=uuid4(), platform="BOSS", cursor={})
    adapter = BossMessageDiscoveryAdapter(get_browser_selectors())
    monkeypatch.setattr(
        adapter,
        "scan",
        MagicMock(side_effect=ValueError("消息页缺失")),
    )
    ensure = MagicMock(return_value=True)
    monkeypatch.setattr(adapter, "ensure_list_page", ensure)
    paused = MagicMock()
    events: list[str] = []
    monkeypatch.setattr("scripts.run_agent_worker.pause_run", paused)
    monkeypatch.setattr(
        "scripts.run_agent_worker.get_settings",
        lambda: MagicMock(agent_tick_batch_size=10),
    )
    monkeypatch.setattr(
        "scripts.run_agent_worker.runtime_event",
        lambda _logger, event, **_values: events.append(event),
    )

    assert not _discover_messages(
        session,
        run,
        "worker-1",
        "http://127.0.0.1:9222",
        adapter,
    )
    ensure.assert_called_once_with("http://127.0.0.1:9222")
    paused.assert_not_called()
    session.commit.assert_called_once()
    assert events == ["PLATFORM_PAGE_REOPENED"]


def test_disabled_maimai_recommendations_do_not_block_ordinary_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    run = db.AgentRun(id=uuid4(), platform="MAIMAI")
    scan = MagicMock()
    monkeypatch.setattr(
        "scripts.run_agent_worker._effective_rules",
        lambda *_: AutomationRules(
            enabled=True,
            auto_reply_enabled=True,
            maimai_recommendation_enabled=False,
        ),
    )
    monkeypatch.setattr("scripts.run_agent_worker.scan_recommendations", scan)

    assert _process_maimai_recommendations(
        session,
        run,
        "worker-1",
        "http://127.0.0.1:9222",
        FakeActionExecutor(),
    )
    scan.assert_not_called()


def test_raw_reply_waits_for_button_and_delayed_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedRawPage:
        def __init__(self, _: str) -> None:
            self.url = "https://www.zhipin.com/web/geek/chat"
            self.send_checks = 0
            self.readback_checks = 0

        def __enter__(self) -> "DelayedRawPage":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def _evaluate(self, script: str) -> bool:
            if "element.textContent = value" in script:
                return True
            if "element.click()" in script:
                self.send_checks += 1
                return self.send_checks >= 3
            if ".some(item =>" in script:
                self.readback_checks += 1
                return self.readback_checks >= 4
            return False

    page = DelayedRawPage("ws://fixture")
    monkeypatch.setattr(
        "adapters.browser.playwright_actions.RawCdpPageReader", lambda _: page
    )
    monkeypatch.setattr("adapters.browser.playwright_actions.time.sleep", lambda _: None)
    executor = PlaywrightActionExecutor(get_browser_selectors())
    result = executor._send_reply_on_raw_page(
        "ws://fixture",
        command().model_copy(
            update={
                "platform": "BOSS",
                "conversation_key": "conversation-1",
            }
        ),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert page.send_checks == 3
    assert page.readback_checks == 4
