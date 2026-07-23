from enum import StrEnum


class ActionType(StrEnum):
    GREETING = "GREETING"
    REPLY = "REPLY"
    RESUME = "RESUME"
    LOW_SCORE_DECLINE = "LOW_SCORE_DECLINE"
    MISMATCH_DECLINE = "MISMATCH_DECLINE"
    SCHEDULE_REPLY = "SCHEDULE_REPLY"


class ActionStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


ALLOWED_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PENDING_APPROVAL: {
        ActionStatus.APPROVED, ActionStatus.CANCELLED,
        ActionStatus.EXPIRED, ActionStatus.SUPERSEDED,
    },
    ActionStatus.APPROVED: {ActionStatus.EXECUTING, ActionStatus.CANCELLED, ActionStatus.EXPIRED},
    ActionStatus.EXECUTING: {
        ActionStatus.SUCCEEDED, ActionStatus.FAILED_RETRYABLE,
        ActionStatus.FAILED_FINAL, ActionStatus.OUTCOME_UNKNOWN,
    },
    ActionStatus.FAILED_RETRYABLE: {ActionStatus.APPROVED, ActionStatus.CANCELLED},
    ActionStatus.SUCCEEDED: set(), ActionStatus.FAILED_FINAL: set(),
    ActionStatus.CANCELLED: set(), ActionStatus.EXPIRED: set(),
    ActionStatus.SUPERSEDED: set(), ActionStatus.OUTCOME_UNKNOWN: set(),
}


def require_transition(current: str, target: ActionStatus) -> None:
    status = ActionStatus(current)
    if target not in ALLOWED_TRANSITIONS[status]:
        raise ValueError(f"非法动作状态转换: {status.value} -> {target.value}")
