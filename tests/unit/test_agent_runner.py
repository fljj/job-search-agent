from adapters.browser.fake_actions import FakeActionExecutor
from packages.browser_worker.actions import (
    ApprovedCommand,
    ExecutionOutcome,
    ExecutionResult,
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
