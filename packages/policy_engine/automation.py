from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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
    low_score_decline_enabled: bool = True
    auto_reply_min_confidence: float = Field(default=0.90, ge=0.75, le=1)
    auto_resume_enabled: bool = False
    auto_resume_min_score: int = Field(default=60, ge=60, le=100)
    maimai_recommendation_enabled: bool = False
    maimai_recommendation_resume_enabled: bool = False
    hourly_limit: int = Field(default=10, ge=1, le=100)
    daily_limit: int = Field(default=50, ge=1, le=1000)
    emergency_stop: bool = False
    job_scan_enabled: bool = False
    hourly_scan_limit: int = Field(default=100, ge=1, le=1000)
    daily_scan_limit: int = Field(default=500, ge=1, le=10000)
    company_cooldown_hours: int = Field(default=24, ge=0, le=720)
    recruiter_cooldown_hours: int = Field(default=24, ge=0, le=720)
    work_start_hour: int = Field(default=8, ge=0, le=23)
    work_end_hour: int = Field(default=22, ge=1, le=24)

    @model_validator(mode="after")
    def validate_work_hours(self) -> "AutomationRules":
        if self.work_start_hour >= self.work_end_hour:
            raise ValueError("自动化工作开始时间必须早于结束时间")
        return self


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
    qualification_status: str = "UNKNOWN"
    hourly_count: int = 0
    daily_count: int = 0


def evaluate_automation(
    context: AutomationContext, rules: AutomationRules
) -> tuple[AutomationDecision, list[str]]:
    """用确定性顺序评估自动动作，前序拒绝不能被后序条件覆盖。"""
    if rules.emergency_stop:
        return AutomationDecision.DENY, ["EMERGENCY_STOP_ACTIVE"]
    if not rules.enabled or rules.paused:
        return AutomationDecision.DENY, ["AUTOMATION_DISABLED_OR_PAUSED"]
    if context.hourly_count >= rules.hourly_limit or context.daily_count >= rules.daily_limit:
        return AutomationDecision.DENY, ["RATE_LIMIT_REACHED"]
    if context.action_type == "GREETING":
        if not context.eligible or not context.job_open:
            return AutomationDecision.DENY, ["JOB_NOT_ELIGIBLE_OR_OPEN"]
        if context.grade == "C":
            return AutomationDecision.DENY, ["C_GRADE_NO_AUTO"]
        if not rules.auto_greet_enabled:
            return AutomationDecision.DENY, ["AUTO_GREETING_DISABLED"]
        if context.score < rules.auto_greet_min_score:
            return AutomationDecision.DENY, ["GREETING_SCORE_BELOW_THRESHOLD"]
        return AutomationDecision.ALLOW_AUTO, ["GREETING_POLICY_MATCHED"]

    if context.action_type in {"REPLY", "MISMATCH_DECLINE"}:
        if "INTERVIEW_TIME" in context.intents:
            return AutomationDecision.REQUIRE_CONFIRMATION, ["SPECIFIC_TIME_REQUIRES_CONFIRMATION"]
        if context.original_decision != "ALLOW_AUTO":
            return AutomationDecision.DENY, ["REPLY_NOT_AUTHORIZED"]
        if not rules.auto_reply_enabled:
            return AutomationDecision.DENY, ["AUTO_REPLY_DISABLED"]
        if (
            context.action_type == "REPLY"
            and context.qualification_status == "MISMATCH"
        ):
            return AutomationDecision.DENY, ["QUALIFICATION_MISMATCH"]
        if (
            context.action_type == "MISMATCH_DECLINE"
            and context.qualification_status != "MISMATCH"
        ):
            return AutomationDecision.DENY, ["MISMATCH_NOT_ESTABLISHED"]
        return AutomationDecision.ALLOW_AUTO, [
            f"{context.action_type}_POLICY_MATCHED"
        ]

    if context.action_type == "RESUME":
        if context.resume_already_sent:
            return AutomationDecision.DENY, ["RESUME_ALREADY_SENT"]
        if not context.explicit_resume_request:
            return AutomationDecision.DENY, ["RESUME_NOT_EXPLICITLY_REQUESTED"]
        if not context.resume_available:
            return AutomationDecision.DENY, ["RESUME_NOT_AVAILABLE"]
        if context.qualification_status == "MISMATCH":
            return AutomationDecision.DENY, ["QUALIFICATION_MISMATCH"]
        if not rules.auto_resume_enabled:
            return AutomationDecision.DENY, ["AUTO_RESUME_DISABLED"]
        return AutomationDecision.ALLOW_AUTO, ["INBOUND_RESUME_POLICY_MATCHED"]
    return AutomationDecision.DENY, ["UNSUPPORTED_ACTION"]
