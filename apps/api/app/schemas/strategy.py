from uuid import UUID

from packages.job_matching.models import Strategy


class StrategyPayload(Strategy):
    candidate_profile_id: UUID


class StrategyResponse(StrategyPayload):
    id: UUID
