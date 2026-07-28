import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class TelegramChannelConfig(BaseModel):
    channel_id: str = Field(pattern=r"^-100\d+$")
    name: str = Field(min_length=1)


class TelegramPolicyConfig(BaseModel):
    channels: list[TelegramChannelConfig] = Field(min_length=1)
    scan_limit_per_channel: int = Field(default=30, ge=1, le=100)
    retry_delay_seconds: int = Field(default=300, ge=60, le=3600)


@lru_cache
def get_telegram_policy() -> TelegramPolicyConfig:
    path = Path(__file__).resolve().parents[4] / "config" / "telegram-policy.json"
    return TelegramPolicyConfig.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
