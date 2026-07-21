from uuid import UUID

from pydantic import BaseModel, Field


class ApproveRequest(BaseModel):
    conversation_id: UUID


class ModifyRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class ResumeConfirmationRequest(BaseModel):
    conversation_id: UUID
    resume_id: UUID


class ExecuteRequest(BaseModel):
    cdp_url: str = "http://127.0.0.1:9222"


class ActionResponse(BaseModel):
    id: UUID
    confirmation_task_id: UUID | None
    action_type: str
    status: str
    conversation_id: UUID
    content: str | None
    attachment_name: str | None
    failure_code: str | None
    version: int
