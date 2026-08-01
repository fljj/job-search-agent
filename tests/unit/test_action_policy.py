import pytest

from apps.api.app.models import entities as db
from apps.api.app.services.automation_service import _liepin_inbound_identity_gaps
from packages.policy_engine.content_check import validate_edited_content
from packages.policy_engine.state_machine import ActionStatus, ActionType, require_transition


def test_only_approved_action_can_start_execution() -> None:
    require_transition("APPROVED", ActionStatus.EXECUTING)
    with pytest.raises(ValueError):
        require_transition("PENDING_APPROVAL", ActionStatus.EXECUTING)


def test_outcome_unknown_can_only_enter_reconciliation_execution() -> None:
    require_transition("OUTCOME_UNKNOWN", ActionStatus.EXECUTING)
    with pytest.raises(ValueError):
        require_transition("OUTCOME_UNKNOWN", ActionStatus.APPROVED)


def test_edited_sensitive_content_is_rejected() -> None:
    assert validate_edited_content("我的身份证号是 123") == ["SENSITIVE_OR_PROHIBITED"]
    assert validate_edited_content("我有 8 年 Java 经验") == []


def test_mismatch_decline_has_a_stable_action_type() -> None:
    assert ActionType.MISMATCH_DECLINE.value == "MISMATCH_DECLINE"


def test_liepin_mismatch_decline_accepts_reliable_observed_job_identity() -> None:
    conversation = db.Conversation(
        platform="LIEPIN",
        external_conversation_id="conversation-1",
        recruiter_name="招聘人",
        observed_company_name="测试公司",
        observed_job_title="测试岗位",
    )

    assert _liepin_inbound_identity_gaps(
        conversation, None, allow_observed_job_identity=True
    ) == []
    assert _liepin_inbound_identity_gaps(conversation, None) == [
        "LIEPIN_LINKED_JOB_ID_MISSING",
        "LIEPIN_LINKED_JOB_IDENTITY_INCOMPLETE",
    ]


@pytest.mark.parametrize("action_type", [
    "RESUME_CONSENT_ACCEPT",
    "CONTACT_CONSENT_ACCEPT",
    "LOCATION_CONSENT_ACCEPT",
    "PLATFORM_RECOMMENDATION_ACCEPT",
    "PLATFORM_RECOMMENDATION_REJECT",
])
def test_platform_write_actions_have_stable_action_types(action_type: str) -> None:
    assert ActionType(action_type).value == action_type
