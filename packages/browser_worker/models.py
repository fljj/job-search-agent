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
    JOB_LIST = "JOB_LIST"
    JOB = "JOB"
    CONVERSATION_LIST = "CONVERSATION_LIST"
    CONVERSATION = "CONVERSATION"


class MessageDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class BrowserJobSummary(BaseModel):
    external_job_id: str
    title: str
    company_name: str
    detail_url: str | None = None


class BrowserJob(BaseModel):
    external_job_id: str | None = None
    title: str
    company_name: str
    industry: str | None = None
    location: str | None = None
    work_mode: str = "UNKNOWN"
    salary_text: str | None = None
    recruiter_name: str | None = None
    description: str
    source_status: str = "UNKNOWN"


class BrowserMessage(BaseModel):
    external_message_id: str
    content: str
    received_at: datetime
    direction: MessageDirection = MessageDirection.INBOUND


class BrowserConversationSummary(BaseModel):
    external_conversation_id: str
    recruiter_name: str
    job_title: str | None = None
    company_name: str | None = None
    external_job_id: str | None = None
    last_message_id: str | None = None
    last_message_text: str | None = None
    category: str = "ALL"
    unread_count: int = 0


class BrowserConversation(BaseModel):
    external_conversation_id: str
    recruiter_name: str
    job_title: str | None = None
    company_name: str | None = None
    external_job_id: str | None = None
    messages: list[BrowserMessage] = Field(default_factory=list)


class ReadResult(BaseModel):
    platform: Platform
    status: SessionStatus
    page_type: PageType | None = None
    page_url: str
    page_title: str
    content_hash: str
    selector_version: str
    cursor: str | None = None
    jobs: list[BrowserJobSummary] = Field(default_factory=list)
    conversations: list[BrowserConversationSummary] = Field(default_factory=list)
    job: BrowserJob | None = None
    conversation: BrowserConversation | None = None
    reason_codes: list[str] = Field(default_factory=list)
