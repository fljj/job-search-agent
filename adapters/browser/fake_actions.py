from collections.abc import Iterable

from packages.browser_worker.actions import (
    ApprovedCommand,
    ExecutionOutcome,
    ExecutionResult,
)


class FakeActionExecutor:
    """阶段五离线执行器；不连接浏览器或招聘平台。"""

    def __init__(self, results: Iterable[ExecutionResult] | None = None) -> None:
        self._results = iter(results or [])
        self.commands: list[ApprovedCommand] = []

    def execute(self, cdp_url: str, command: ApprovedCommand) -> ExecutionResult:
        self.commands.append(command)
        return next(
            self._results,
            ExecutionResult(
                outcome=ExecutionOutcome.SUCCEEDED,
                external_reference=f"fake:{len(self.commands)}",
            ),
        )
