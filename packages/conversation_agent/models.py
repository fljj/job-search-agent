from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class Intent(StrEnum):
    JOB_DETAIL = "JOB_DETAIL"
    TECH_STACK = "TECH_STACK"
    WORK_EXPERIENCE = "WORK_EXPERIENCE"
    PROJECT_EXPERIENCE = "PROJECT_EXPERIENCE"
    MANAGEMENT_EXPERIENCE = "MANAGEMENT_EXPERIENCE"
    SALARY = "SALARY"
    LOCATION = "LOCATION"
    REMOTE_POLICY = "REMOTE_POLICY"
    ARRIVAL_DATE = "ARRIVAL_DATE"
    RESUME_REQUEST = "RESUME_REQUEST"
    PHONE_CALL = "PHONE_CALL"
    INTERVIEW_INVITATION = "INTERVIEW_INVITATION"
    INTERVIEW_TIME = "INTERVIEW_TIME"
    COMPANY_INTRODUCTION = "COMPANY_INTRODUCTION"
    UNCLEAR = "UNCLEAR"
    SENSITIVE = "SENSITIVE"


class Decision(StrEnum):
    ALLOW_AUTO = "ALLOW_AUTO"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    DENY = "DENY"


class DraftResult(BaseModel):
    content: str
    intents: list[Intent]
    fact_ids: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    risk_codes: list[str] = Field(default_factory=list)
    decision: Decision
    reason_codes: list[str]


class ConversationPolicyConfig(BaseModel):
    auto_reply_min_confidence: float = Field(default=0.90, ge=0, le=1)
    confirmation_min_confidence: float = Field(default=0.75, ge=0, le=1)
    missing_fact_reply: str = "这部分信息我需要确认一下，稍后回复您。"
    max_greeting_facts: int = Field(default=3, ge=1, le=5)
    max_greeting_skills: int = Field(default=3, ge=1, le=5)
