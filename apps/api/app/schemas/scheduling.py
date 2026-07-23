from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from packages.scheduling.models import SchedulingConfig


class CalendarEventPayload(BaseModel):
    external_event_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    end_at: datetime
    availability: str = "BUSY"

    @model_validator(mode="after")
    def validate_range(self) -> "CalendarEventPayload":
        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            raise ValueError("日历事件时间必须包含时区")
        if self.end_at <= self.start_at:
            raise ValueError("日历事件结束时间必须晚于开始时间")
        return self


class AnalyzeInvitationRequest(BaseModel):
    message_id: UUID
    calendar_available: bool = True


class ApproveScheduleRequest(BaseModel):
    reply_content: str = Field(min_length=1, max_length=2000)
    selected_start_at: datetime | None = None
    selected_end_at: datetime | None = None
    create_calendar_event: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "ApproveScheduleRequest":
        if bool(self.selected_start_at) != bool(self.selected_end_at):
            raise ValueError("候选开始和结束时间必须同时提供")
        if self.selected_start_at is not None and self.selected_end_at is not None:
            if (
                self.selected_start_at.utcoffset() is None
                or self.selected_end_at.utcoffset() is None
            ):
                raise ValueError("候选时间必须包含时区")
            if self.selected_end_at <= self.selected_start_at:
                raise ValueError("候选结束时间必须晚于开始时间")
        return self


class ExecuteScheduleRequest(BaseModel):
    cdp_url: str = "http://127.0.0.1:9222"


class SchedulingPreferencePayload(SchedulingConfig):
    version: int = Field(default=1, ge=1)
