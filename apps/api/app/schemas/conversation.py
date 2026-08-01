from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from packages.conversation_agent.models import Decision, Intent, ReplySource
from packages.knowledge_base.models import Sensitivity


class KnowledgeItemPayload(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    key: str = Field(min_length=1, max_length=150)
    fact: str = Field(min_length=1, max_length=4000)
    source: str = Field(min_length=1, max_length=100)
    allowed_for_auto_reply: bool = False
    sensitivity: Sensitivity = Sensitivity.NORMAL
    verified_at: datetime
    valid_until: datetime | None = None
    version: int | None = None


class KnowledgeItemResponse(KnowledgeItemPayload):
    id: UUID
    version: int


class ResumePayload(BaseModel):
    platform: str = "MOCK"
    attachment_name: str = Field(min_length=1, max_length=255)
    target_directions: list[str] = Field(min_length=1)
    is_available: bool = True
    version: int | None = None


class ResumeResponse(ResumePayload):
    id: UUID
    version: int


class ConversationPayload(BaseModel):
    job_id: UUID | None = None
    external_conversation_id: str = Field(min_length=1, max_length=200)
    recruiter_name: str = Field(min_length=1, max_length=100)
    platform: str = "MOCK"
    recruiter_role: str = "UNKNOWN"
    identity_reliable: bool = True


class MessagePayload(BaseModel):
    external_message_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    received_at: datetime
    identity_reliable: bool = True
    direction: Literal["INBOUND", "OUTBOUND"] = "INBOUND"


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    external_message_id: str
    content: str
    intents: list[Intent]


class DraftResponse(BaseModel):
    id: UUID
    draft_type: str
    content: str
    reply_source: ReplySource | None = None
    intents: list[Intent]
    fact_ids: list[UUID]
    confidence: float
    risk_codes: list[str]
    decision: Decision
    reason_codes: list[str]
    confirmation_task_id: UUID | None = None
    resume_id: UUID | None = None


class GreetingRequest(BaseModel):
    job_score_id: UUID


class ReplyRequest(BaseModel):
    message_id: UUID


class ResumeDraftRequest(BaseModel):
    message_id: UUID


class DraftEditRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
