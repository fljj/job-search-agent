from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Platform(StrEnum):
    BOSS = "BOSS"
    MAIMAI = "MAIMAI"


class SessionStatus(StrEnum):
    SESSION_READY = "SESSION_READY"
    SESSION_AUTH_REQUIRED = "SESSION_AUTH_REQUIRED"
    SESSION_PAGE_CHANGED = "SESSION_PAGE_CHANGED"
    SESSION_TARGET_MISMATCH = "SESSION_TARGET_MISMATCH"
    SESSION_PAUSED = "SESSION_PAUSED"


class PageType(StrEnum):
    JOB = "JOB"
    CONVERSATION = "CONVERSATION"


class BrowserJob(BaseModel):
    external_job_id: str | None = None
    title: str
    company_name: str
    industry: str | None = None
    location: str | None = None
    work_mode: str = "UNKNOWN"
    salary_text: str | None = None
    description: str


class BrowserMessage(BaseModel):
    external_message_id: str
    content: str
    received_at: datetime


class BrowserConversation(BaseModel):
    external_conversation_id: str
    recruiter_name: str
    messages: list[BrowserMessage] = Field(default_factory=list)


class ReadResult(BaseModel):
    platform: Platform
    status: SessionStatus
    page_type: PageType | None = None
    page_url: str
    page_title: str
    content_hash: str
    selector_version: str
    job: BrowserJob | None = None
    conversation: BrowserConversation | None = None
    reason_codes: list[str] = Field(default_factory=list)
