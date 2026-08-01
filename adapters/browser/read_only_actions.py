from packages.browser_worker.actions import (
    ApprovedCommand,
    ExecutionOutcome,
    ExecutionResult,
)


class ReadOnlyActionExecutor:
    """未启用平台写能力时的安全执行器；任何误调用都确定性失败。"""

    def execute(self, _cdp_url: str, _command: ApprovedCommand) -> ExecutionResult:
        return ExecutionResult(
            outcome=ExecutionOutcome.FAILED_FINAL,
            error_code="PLATFORM_WRITES_NOT_ENABLED",
            write_started=False,
        )
