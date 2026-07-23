from uuid import UUID

from pydantic import BaseModel, Field


class ApproveRequest(BaseModel):
    conversation_id: UUID | None = None


class ModifyRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class ExecuteRequest(BaseModel):
    cdp_url: str = "http://127.0.0.1:9222"


class ReconcileRequest(BaseModel):
    cdp_url: str = "http://127.0.0.1:9222"


class ActionResponse(BaseModel):
    id: UUID
    confirmation_task_id: UUID | None
    action_type: str
    status: str
    job_id: UUID | None
    conversation_id: UUID | None
    content: str | None
    delivery_mode: str
    expected_platform_content: str | None
    observed_content: str | None
    attachment_name: str | None
    failure_code: str | None
    version: int
