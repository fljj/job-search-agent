from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class Sensitivity(StrEnum):
    NORMAL = "NORMAL"
    SENSITIVE = "SENSITIVE"
    PROHIBITED = "PROHIBITED"


class KnowledgeFact(BaseModel):
    id: UUID | None = None
    category: str
    key: str
    fact: str
    source: str
    allowed_for_auto_reply: bool = False
    sensitivity: Sensitivity = Sensitivity.NORMAL
    verified_at: datetime
    valid_until: datetime | None = None
    version: int = Field(default=1, ge=1)

    def is_current(self, now: datetime) -> bool:
        return self.valid_until is None or self.valid_until >= now
