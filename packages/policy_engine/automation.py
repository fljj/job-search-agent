from enum import StrEnum

from pydantic import BaseModel, Field


class AutomationDecision(StrEnum):
    ALLOW_AUTO = "ALLOW_AUTO"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    DENY = "DENY"


class AutomationRules(BaseModel):
    enabled: bool = False
    paused: bool = False
    auto_greet_enabled: bool = False
    auto_greet_min_score: int = Field(default=80, ge=80, le=100)
    auto_reply_enabled: bool = False
    auto_reply_min_confidence: float = Field(default=0.90, ge=0.75, le=1)
    auto_resume_enabled: bool = False
    auto_resume_min_score: int = Field(default=60, ge=60, le=100)
    hourly_limit: int = Field(default=10, ge=1, le=100)
    daily_limit: int = Field(default=50, ge=1, le=1000)


class AutomationContext(BaseModel):
    action_type: str
    score: int
    grade: str
    eligible: bool
    job_open: bool
    confidence: float = 1
    original_decision: str = "ALLOW_AUTO"
    intents: list[str] = Field(default_factory=list)
    has_verified_facts: bool = True
    explicit_resume_request: bool = False
    resume_available: bool = False
    resume_already_sent: bool = False
    whitelisted_c: bool = False
    hourly_count: int = 0
    daily_count: int = 0


SENSITIVE_INTENTS = {
    "SALARY", "ARRIVAL_DATE", "PHONE_CALL", "INTERVIEW_INVITATION",
    "INTERVIEW_TIME", "SENSITIVE",
}


def evaluate_automation(
    context: AutomationContext, rules: AutomationRules
) -> tuple[AutomationDecision, list[str]]:
    """用确定性顺序评估自动动作，前序拒绝不能被后序条件覆盖。"""
    if not rules.enabled or rules.paused:
        return AutomationDecision.DENY, ["AUTOMATION_DISABLED_OR_PAUSED"]
    if not context.eligible or not context.job_open:
        return AutomationDecision.DENY, ["JOB_NOT_ELIGIBLE_OR_OPEN"]
    if context.hourly_count >= rules.hourly_limit or context.daily_count >= rules.daily_limit:
        return AutomationDecision.DENY, ["RATE_LIMIT_REACHED"]
    if context.grade == "C":
        return AutomationDecision.REQUIRE_CONFIRMATION if context.whitelisted_c else AutomationDecision.DENY, ["C_GRADE_NO_AUTO"]

    if context.action_type == "GREETING":
        if not rules.auto_greet_enabled:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["AUTO_GREETING_DISABLED"]
        if context.score < rules.auto_greet_min_score:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["GREETING_SCORE_BELOW_THRESHOLD"]
        return AutomationDecision.ALLOW_AUTO, ["GREETING_POLICY_MATCHED"]

    if context.action_type == "REPLY":
        if set(context.intents) & SENSITIVE_INTENTS:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["SENSITIVE_OR_TIME_INTENT"]
        if not context.has_verified_facts or context.original_decision != "ALLOW_AUTO":
            return AutomationDecision.REQUIRE_CONFIRMATION, ["REPLY_REQUIRES_CONFIRMATION"]
        if not rules.auto_reply_enabled or context.confidence < rules.auto_reply_min_confidence:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["REPLY_CONFIDENCE_OR_SWITCH"]
        return AutomationDecision.ALLOW_AUTO, ["REPLY_POLICY_MATCHED"]

    if context.action_type == "RESUME":
        if context.resume_already_sent:
            return AutomationDecision.DENY, ["RESUME_ALREADY_SENT"]
        if not context.explicit_resume_request:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["RESUME_NOT_EXPLICITLY_REQUESTED"]
        if not context.resume_available:
            return AutomationDecision.DENY, ["RESUME_NOT_AVAILABLE"]
        if not rules.auto_resume_enabled or context.score < rules.auto_resume_min_score:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["RESUME_SCORE_OR_SWITCH"]
        return AutomationDecision.ALLOW_AUTO, ["RESUME_POLICY_MATCHED"]
    return AutomationDecision.DENY, ["UNSUPPORTED_ACTION"]
