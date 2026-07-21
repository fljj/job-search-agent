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
