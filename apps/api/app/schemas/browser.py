from uuid import UUID

from pydantic import BaseModel, Field

from packages.browser_worker.models import (
    BrowserConversationSummary,
    BrowserJobSummary,
    PageType,
    Platform,
    SessionStatus,
)


class BrowserReadRequest(BaseModel):
    platform: Platform
    cdp_url: str = "http://127.0.0.1:9222"
    job_id: UUID | None = None
    expected_company: str | None = Field(default=None, max_length=200)
    expected_job_title: str | None = Field(default=None, max_length=200)
    expected_recruiter: str | None = Field(default=None, max_length=100)


class BrowserReadResponse(BaseModel):
    id: UUID
    platform: Platform
    status: SessionStatus
    page_type: PageType | None
    reason_codes: list[str]
    cursor: str | None
    jobs: list[BrowserJobSummary]
    conversations: list[BrowserConversationSummary]
    imported_job_id: UUID | None
    imported_conversation_id: UUID | None
    imported_message_ids: list[UUID]
    evidence_id: UUID
    duplicate: bool = False


class PlatformSessionResponse(BaseModel):
    id: UUID
    platform: Platform
    status: SessionStatus
    last_reason_codes: list[str]
