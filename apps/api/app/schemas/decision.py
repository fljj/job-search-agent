from uuid import UUID

from pydantic import BaseModel

from packages.job_matching.models import JobDecisionResult


class DecisionRequest(BaseModel):
    strategy_id: UUID
    candidate_profile_id: UUID
    parsed_job_detail_id: UUID | None = None


class BatchDecisionRequest(BaseModel):
    job_ids: list[UUID]
    strategy_id: UUID
    candidate_profile_id: UUID


class DecisionResponse(JobDecisionResult):
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
