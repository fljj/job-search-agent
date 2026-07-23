from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.models import entities as db
from apps.api.app.schemas.recommendation import (
    RecommendationActionRequest,
    RecommendationScanRequest,
)
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.recommendation_service import (
    dispatch_recommendation,
    get_recommendation,
    list_recommendations,
    reconcile_recommendation,
    scan_recommendations,
)

router = APIRouter(tags=["platform-recommendations"])


@router.get("/platform-recommendations")
def items(
    platform: str | None = None,
    decision: str | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        {
            "items": list_recommendations(
                session, platform=platform, decision=decision, status=status
            )
        }
    )


@router.get("/platform-recommendations/{recommendation_id}")
def item(
    recommendation_id: UUID,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(get_recommendation(session, recommendation_id))


@router.post("/platform-recommendations/scan")
def scan(
    payload: RecommendationScanRequest,
    _idempotency_key: str = Header(alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    run = session.get(db.AgentRun, payload.run_id)
    if run is None:
        raise ResourceNotFoundError("Agent 运行不存在")
    return response(
        {
            "items": scan_recommendations(
                session, run, payload.cdp_url, limit=payload.limit
            )
        }
    )


@router.post("/platform-recommendations/{recommendation_id}/dispatch")
def dispatch(
    recommendation_id: UUID,
    payload: RecommendationActionRequest,
    _idempotency_key: str = Header(alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        dispatch_recommendation(session, recommendation_id, payload.cdp_url)
    )


@router.post("/platform-recommendations/{recommendation_id}/reconcile")
def reconcile(
    recommendation_id: UUID,
    payload: RecommendationActionRequest,
    _idempotency_key: str = Header(alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        reconcile_recommendation(session, recommendation_id, payload.cdp_url)
    )
