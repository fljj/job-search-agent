from uuid import UUID

from packages.scoring.models import Strategy


class StrategyPayload(Strategy):
    candidate_profile_id: UUID


class StrategyResponse(StrategyPayload):
    id: UUID
