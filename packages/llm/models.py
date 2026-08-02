from decimal import Decimal
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from packages.conversation_agent.models import Intent

T = TypeVar("T")


class LlmCallMetadata(BaseModel):
    provider: str
    model: str
    prompt_version: str
    response_id: str | None = None
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    attempt_number: int = Field(default=1, ge=1)


class LlmResult[T](BaseModel):
    data: T
    metadata: LlmCallMetadata


class JobContactDecisionRequest(BaseModel):
    job: dict[str, object]
    requirements: dict[str, object]
    candidate: dict[str, object]
    strategy: dict[str, object]


class JobContactDecisionOutput(BaseModel):
    decision: Literal["CONTACT", "SKIP", "REVIEW"]
    confidence: Decimal = Field(ge=0, le=1)
    matched_evidence: list[str] = Field(default_factory=list, max_length=3)
    uncertainties: list[str] = Field(default_factory=list, max_length=3)
    reason: str = Field(min_length=1, max_length=200)


class MessageClassificationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    recent_messages: list[str] = Field(default_factory=list, max_length=20)


class MessageClassification(BaseModel):
    intents: list[Intent] = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)


class ConversationTurn(BaseModel):
    direction: str = Field(pattern="^(INBOUND|OUTBOUND)$")
    content: str = Field(min_length=1, max_length=10000)


class ConversationMemory(BaseModel):
    discussed_topics: list[str] = Field(default_factory=list)
    candidate_asked_topics: list[str] = Field(default_factory=list)
    confirmed_topics: list[str] = Field(default_factory=list)
    confirmed_details: dict[str, str] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    completed_actions: list[str] = Field(default_factory=list)


class TrustedFact(BaseModel):
    id: UUID
    content: str = Field(min_length=1, max_length=2000)


class GreetingRequest(BaseModel):
    company_name: str
    job_title: str
    matched_skills: list[str] = Field(default_factory=list, max_length=5)
    facts: list[TrustedFact] = Field(default_factory=list, max_length=5)


class ReplyContext(BaseModel):
    company_name: str
    job_title: str
    job_location: str | None = None
    work_mode: str
    required_skills: list[str] = Field(default_factory=list, max_length=30)
    preferred_skills: list[str] = Field(default_factory=list, max_length=30)
    contact_decision: str
    decision_reason: str
    matched_evidence: list[str] = Field(default_factory=list, max_length=3)
    uncertainties: list[str] = Field(default_factory=list, max_length=3)
    enabled_work_modes: list[str] = Field(default_factory=list)
    allowed_onsite_locations: list[str] = Field(default_factory=list)
    remote_preferred: bool = False


class ReplyRequest(BaseModel):
    incoming_message: str = Field(min_length=1, max_length=10000)
    recent_messages: list[str] = Field(default_factory=list, max_length=20)
    recent_turns: list[ConversationTurn] = Field(default_factory=list, max_length=20)
    conversation_memory: ConversationMemory = Field(default_factory=ConversationMemory)
    facts: list[TrustedFact] = Field(default_factory=list, max_length=20)
    context: ReplyContext


class GeneratedMessage(BaseModel):
    content: str = Field(min_length=1, max_length=120)
    fact_ids: list[UUID] = Field(default_factory=list)
    confidence: Decimal = Field(ge=0, le=1)
    risk_codes: list[str] = Field(default_factory=list)


class ConversationMessage(BaseModel):
    id: UUID
    content: str = Field(min_length=1, max_length=10000)


class ConversationEvaluationRequest(BaseModel):
    messages: list[ConversationMessage] = Field(min_length=1, max_length=50)


class ConversationEvaluation(BaseModel):
    resume_requested: bool
    positive_feedback: bool
    evidence_message_ids: list[UUID] = Field(default_factory=list)
    confidence: Decimal = Field(ge=0, le=1)
