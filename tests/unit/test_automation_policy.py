import pytest

from packages.policy_engine.automation import (
    AutomationContext,
    AutomationDecision,
    AutomationRules,
    evaluate_automation,
)


def context(**changes: object) -> AutomationContext:
    values: dict[str, object] = {
        "action_type": "GREETING", "score": 70, "grade": "A",
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


def test_a_grade_greeting_is_auto_and_b_requires_explicit_60_threshold() -> None:
    assert evaluate_automation(context(), rules())[0] is AutomationDecision.ALLOW_AUTO
    b_grade = context(score=60, grade="B")
    assert evaluate_automation(b_grade, rules())[0] is AutomationDecision.REQUIRE_CONFIRMATION
    assert evaluate_automation(b_grade, rules(auto_greet_min_score=60))[0] is AutomationDecision.ALLOW_AUTO


@pytest.mark.parametrize("intent", [
    "SALARY", "ARRIVAL_DATE", "PHONE_CALL", "INTERVIEW_INVITATION", "INTERVIEW_TIME", "SENSITIVE",
])
def test_sensitive_or_time_reply_always_requires_confirmation(intent: str) -> None:
    reply = context(action_type="REPLY", confidence=1, intents=[intent], has_verified_facts=True)
    assert evaluate_automation(reply, rules())[0] is AutomationDecision.REQUIRE_CONFIRMATION


def test_reply_requires_verified_facts_confidence_and_original_allow() -> None:
    reply = context(action_type="REPLY", confidence=.95, intents=["TECH_STACK"], has_verified_facts=True)
    assert evaluate_automation(reply, rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(reply.model_copy(update={"has_verified_facts": False}), rules())[0] is AutomationDecision.REQUIRE_CONFIRMATION
    assert evaluate_automation(reply.model_copy(update={"confidence": .89}), rules())[0] is AutomationDecision.REQUIRE_CONFIRMATION


def test_resume_requires_explicit_request_available_attachment_and_a_grade() -> None:
    resume = context(action_type="RESUME", explicit_resume_request=True, resume_available=True)
    assert evaluate_automation(resume, rules())[0] is AutomationDecision.ALLOW_AUTO
    assert evaluate_automation(resume.model_copy(update={"explicit_resume_request": False}), rules())[0] is AutomationDecision.REQUIRE_CONFIRMATION
    assert evaluate_automation(resume.model_copy(update={"resume_already_sent": True}), rules())[0] is AutomationDecision.DENY


def test_switch_pause_and_rate_limits_stop_automation() -> None:
    assert evaluate_automation(context(), rules(enabled=False))[0] is AutomationDecision.DENY
    assert evaluate_automation(context(), rules(paused=True))[0] is AutomationDecision.DENY
    assert evaluate_automation(context(hourly_count=10), rules(hourly_limit=10))[0] is AutomationDecision.DENY
    assert evaluate_automation(context(daily_count=50), rules(daily_limit=50))[0] is AutomationDecision.DENY
