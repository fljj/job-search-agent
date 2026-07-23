import pytest

from packages.policy_engine.content_check import validate_edited_content
from packages.policy_engine.state_machine import ActionStatus, ActionType, require_transition


def test_only_approved_action_can_start_execution() -> None:
    require_transition("APPROVED", ActionStatus.EXECUTING)
    with pytest.raises(ValueError):
        require_transition("PENDING_APPROVAL", ActionStatus.EXECUTING)


def test_outcome_unknown_is_terminal() -> None:
    with pytest.raises(ValueError):
        require_transition("OUTCOME_UNKNOWN", ActionStatus.APPROVED)


def test_edited_sensitive_content_is_rejected() -> None:
    assert validate_edited_content("我的身份证号是 123") == ["SENSITIVE_OR_PROHIBITED"]
    assert validate_edited_content("我有 8 年 Java 经验") == []


def test_low_score_decline_has_a_stable_action_type() -> None:
    assert ActionType.LOW_SCORE_DECLINE.value == "LOW_SCORE_DECLINE"


def test_mismatch_decline_has_a_stable_action_type() -> None:
    assert ActionType.MISMATCH_DECLINE.value == "MISMATCH_DECLINE"
