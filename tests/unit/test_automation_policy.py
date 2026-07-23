import pytest

from packages.policy_engine.automation import (
    AutomationContext,
    AutomationDecision,
    AutomationRules,
    evaluate_automation,
)


def context(**changes: object) -> AutomationContext:
    values: dict[str, object] = {
        "action_type": "GREETING", "score": 80, "grade": "A",
        "eligible": True, "job_open": True,
    }
    values.update(changes)
    return AutomationContext.model_validate(values)


def rules(**changes: object) -> AutomationRules:
    values: dict[str, object] = {
        "enabled": True, "auto_greet_enabled": True,
        "auto_reply_enabled": True, "auto_resume_enabled": True,
    }
    values.update(changes)
    return AutomationRules.model_validate(values)


@pytest.mark.parametrize("changes", [
    {"eligible": False}, {"job_open": False}, {"grade": "C", "score": 59},
])
def test_ineligible_closed_and_c_grade_never_auto(changes: dict[str, object]) -> None:
    decision, _ = evaluate_automation(context(**changes), rules())
    assert decision is not AutomationDecision.ALLOW_AUTO


def test_greeting_requires_fixed_80_threshold() -> None:
    assert evaluate_automation(context(), rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(
        context(score=79), rules()
    )[0] is AutomationDecision.DENY
    with pytest.raises(ValueError):
        rules(auto_greet_min_score=79)


def test_only_specific_time_reply_requires_confirmation() -> None:
    intent = "INTERVIEW_TIME"
    reply = context(action_type="REPLY", confidence=1, intents=[intent], has_verified_facts=True)
    assert evaluate_automation(reply, rules())[0] is AutomationDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize(
    "intent",
    ["SALARY", "ARRIVAL_DATE", "PHONE_CALL", "INTERVIEW_INVITATION", "SENSITIVE"],
)
def test_non_time_safe_replies_do_not_require_confirmation(intent: str) -> None:
    reply = context(action_type="REPLY", confidence=.5, intents=[intent], has_verified_facts=False)
    assert evaluate_automation(reply, rules())[0] is AutomationDecision.ALLOW_AUTO


def test_reply_requires_original_allow_but_not_model_confidence() -> None:
    reply = context(action_type="REPLY", confidence=.95, intents=["TECH_STACK"], has_verified_facts=True)
    assert evaluate_automation(reply, rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(reply.model_copy(update={"has_verified_facts": False}), rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(reply.model_copy(update={"confidence": .4}), rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(reply.model_copy(update={"original_decision": "DENY"}), rules())[0] is AutomationDecision.DENY


def test_inbound_resume_ignores_score_but_requires_request_attachment_and_match() -> None:
    resume = context(
        action_type="RESUME", score=0, grade="UNKNOWN",
        explicit_resume_request=True, resume_available=True,
        qualification_status="ROUGH_MATCH",
    )
    assert evaluate_automation(resume, rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(resume.model_copy(update={"explicit_resume_request": False}), rules())[0] is AutomationDecision.DENY
    assert evaluate_automation(resume.model_copy(update={"resume_already_sent": True}), rules())[0] is AutomationDecision.DENY
    assert evaluate_automation(
        resume.model_copy(update={"qualification_status": "MISMATCH"}), rules()
    )[0] is AutomationDecision.DENY


def test_mismatch_decline_requires_mismatch_evidence() -> None:
    decline = context(
        action_type="MISMATCH_DECLINE",
        original_decision="ALLOW_AUTO",
        qualification_status="MISMATCH",
    )
    assert evaluate_automation(decline, rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(
        decline.model_copy(update={"qualification_status": "ROUGH_MATCH"}), rules()
    )[0] is AutomationDecision.DENY


def test_switch_pause_and_rate_limits_stop_automation() -> None:
    assert evaluate_automation(context(), rules(enabled=False))[0] is AutomationDecision.DENY
    assert evaluate_automation(context(), rules(paused=True))[0] is AutomationDecision.DENY
    assert evaluate_automation(context(hourly_count=10), rules(hourly_limit=10))[0] is AutomationDecision.DENY
    assert evaluate_automation(context(daily_count=50), rules(daily_limit=50))[0] is AutomationDecision.DENY


def test_emergency_stop_overrides_all_automatic_permissions() -> None:
    decision, reasons = evaluate_automation(
        context(score=100), rules(emergency_stop=True)
    )
    assert decision is AutomationDecision.DENY
    assert reasons == ["EMERGENCY_STOP_ACTIVE"]


def test_historical_low_score_decline_cannot_be_created() -> None:
    decline = context(
        action_type="LOW_SCORE_DECLINE",
        score=59,
        grade="C",
        eligible=False,
    )
    assert evaluate_automation(decline, rules())[0] is AutomationDecision.DENY


def test_normal_reply_is_denied_after_qualification_becomes_mismatch() -> None:
    reply = context(
        action_type="REPLY",
        qualification_status="MISMATCH",
    )
    decision, reasons = evaluate_automation(reply, rules())
    assert decision is AutomationDecision.DENY
    assert reasons == ["QUALIFICATION_MISMATCH"]


def test_invalid_work_hours_are_rejected() -> None:
    with pytest.raises(ValueError):
        rules(work_start_hour=22, work_end_hour=8)
