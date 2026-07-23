from datetime import datetime, time
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class EventType(StrEnum):
    PHONE_CALL = "PHONE_CALL"
    VIDEO_INTERVIEW = "VIDEO_INTERVIEW"
    TECHNICAL_INTERVIEW = "TECHNICAL_INTERVIEW"
    ONSITE_INTERVIEW = "ONSITE_INTERVIEW"


class CalendarStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPLETE = "INCOMPLETE"
    AMBIGUOUS = "AMBIGUOUS"


class SchedulingConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    workday_start: time = time(9)
    workday_end: time = time(18)
    lunch_start: time = time(12)
    lunch_end: time = time(13, 30)
    buffer_before_minutes: int = Field(default=15, ge=0, le=120)
    buffer_after_minutes: int = Field(default=15, ge=0, le=120)
    phone_duration_minutes: int = Field(default=30, ge=10, le=240)
    video_duration_minutes: int = Field(default=60, ge=10, le=240)
    technical_duration_minutes: int = Field(default=90, ge=10, le=300)
    onsite_duration_minutes: int = Field(default=90, ge=10, le=480)
    onsite_commute_minutes: int | None = Field(default=None, ge=0, le=360)
    confirmation_ttl_minutes: int = Field(default=120, ge=5, le=1440)
    calendar_snapshot_ttl_minutes: int = Field(default=15, ge=1, le=120)
    suggestion_count: int = Field(default=3, ge=2, le=3)

    @model_validator(mode="after")
    def validate_availability_window(self) -> "SchedulingConfig":
        if not (
            self.workday_start
            < self.lunch_start
            < self.lunch_end
            < self.workday_end
        ):
            raise ValueError("工作时间和午休时间必须按开始、午休、结束顺序配置")
        return self


class ParsedInvitation(BaseModel):
    event_type: EventType
    start_at: datetime | None
    end_at: datetime | None
    timezone: str
    duration_minutes: int
    location: str | None = None
    confidence: float = Field(ge=0, le=1)
    source_text: str
    risk_codes: list[str] = Field(default_factory=list)


class CalendarBusySlot(BaseModel):
    start_at: datetime
    end_at: datetime
    availability: str = "BUSY"
