from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from packages.policy_engine.automation import AutomationRules


class AutomationSettingPayload(AutomationRules):
    scope_type: Literal["GLOBAL", "PLATFORM", "STRATEGY"]
    scope_key: str = Field(min_length=1, max_length=100)


class AutomationDispatchRequest(BaseModel):
    action_type: Literal[
        "GREETING", "REPLY", "RESUME", "LOW_SCORE_DECLINE", "MISMATCH_DECLINE"
    ]
    conversation_id: UUID
    draft_id: UUID
    resume_id: UUID | None = None
    cdp_url: str = "http://127.0.0.1:9222"


class AutomationDecisionResponse(BaseModel):
    decision: str
    reason_codes: list[str]
    action_id: UUID | None = None
    action_status: str | None = None


class AgentRunStartRequest(BaseModel):
    platform: Literal["MOCK", "BOSS", "MAIMAI"]
    strategy_id: UUID


class AgentRunTickRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)


class AgentRunResponse(BaseModel):
    id: UUID
    platform: str
    strategy_id: UUID
    executor_type: str
    status: str
    heartbeat_at: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    cursor: dict[str, object]
    processed_count: int
    action_count: int
    failure_count: int
    consecutive_failure_count: int
    pause_reason_codes: list[str]
    version: int
