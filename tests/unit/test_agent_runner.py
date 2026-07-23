import pytest

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.playwright_actions import PlaywrightActionExecutor
from apps.api.app.core.browser_config import get_browser_selectors
from packages.browser_worker.actions import (
    ApprovedCommand,
    ExecutionOutcome,
    ExecutionResult,
)
from scripts.run_agent_worker import _build_executor, _single_worker_lock


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


def test_only_one_worker_can_hold_process_lock() -> None:
    with _single_worker_lock(), pytest.raises(RuntimeError, match="已有 Agent Worker"):
        with _single_worker_lock():
            pytest.fail("第二个 Worker 不应取得进程锁")


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
