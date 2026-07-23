from uuid import UUID

from pydantic import BaseModel

from packages.scoring.models import ScoreResult


class ScoreRequest(BaseModel):
    strategy_id: UUID
    candidate_profile_id: UUID
    parsed_job_detail_id: UUID | None = None


class BatchScoreRequest(BaseModel):
    job_ids: list[UUID]
    strategy_id: UUID
    candidate_profile_id: UUID


class ScoreResponse(ScoreResult):
    id: UUID
    job_id: UUID
    strategy_id: UUID
    candidate_profile_id: UUID
    parsed_job_detail_id: UUID
    strategy_version: int
    profile_version: int
    input_fingerprint: str
    prompt_version: str | None = None
    llm_invocation_id: UUID | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_recommends_proactive_contact: bool = False
    llm_contact_reason: str | None = None
    automation_eligible: bool = False
