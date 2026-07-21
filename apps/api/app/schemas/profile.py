from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class SkillPayload(BaseModel):
    name: str = Field(min_length=1)
    years: Decimal | None = Field(default=None, ge=0)
    proficiency: str | None = None
    source: str = Field(min_length=1)
    is_core: bool = False


class IndustryExperiencePayload(BaseModel):
    industry_code: str = Field(min_length=1)
    years: Decimal | None = Field(default=None, ge=0)
    source: str = Field(min_length=1)


class ProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    total_years: Decimal = Field(ge=0)
    management_years: Decimal = Field(default=Decimal(0), ge=0)
    has_architecture_experience: bool = False
    has_core_system_experience: bool = False
    version: int | None = Field(default=None, ge=1)
    skills: list[SkillPayload] = Field(default_factory=list)
    industry_experiences: list[IndustryExperiencePayload] = Field(default_factory=list)


class ProfileResponse(ProfilePayload):
    id: UUID
    version: int
