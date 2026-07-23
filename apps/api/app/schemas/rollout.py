from typing import Literal

from pydantic import BaseModel, Field


class RolloutCreateRequest(BaseModel):
    platform: Literal["BOSS"]
    minimum_stage_hours: int = Field(default=24, ge=24, le=168)
    reply_daily_limit: int = Field(default=5, ge=1, le=5)
    greeting_daily_limit: int = Field(default=3, ge=1, le=3)


class RolloutTransitionRequest(BaseModel):
    action: Literal["ACTIVATE", "PAUSE", "ADVANCE", "ROLLBACK"]
    expected_version: int = Field(ge=1)
