from uuid import UUID

from pydantic import BaseModel, Field


class RecommendationScanRequest(BaseModel):
    run_id: UUID
    cdp_url: str = "http://127.0.0.1:9222"
    limit: int = Field(default=20, ge=1, le=100)


class RecommendationActionRequest(BaseModel):
    cdp_url: str = "http://127.0.0.1:9222"
