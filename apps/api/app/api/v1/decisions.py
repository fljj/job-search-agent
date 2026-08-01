from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from adapters.llm.errors import LlmProviderError
from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.decision import BatchDecisionRequest, DecisionRequest
from apps.api.app.services.decision_service import create_decision, get_decision, list_decisions
from apps.api.app.services.errors import ResourceNotFoundError

router = APIRouter(tags=["job-decisions"])


@router.post("/jobs/decisions/batch")
def decide_batch(
    payload: BatchDecisionRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for job_id in payload.job_ids:
        try:
            result = create_decision(
                session,
                job_id,
                DecisionRequest(
                    strategy_id=payload.strategy_id,
                    candidate_profile_id=payload.candidate_profile_id,
                ),
            )
            items.append({"job_id": job_id, "result": "DECIDED", "decision": result})
        except (ValueError, ResourceNotFoundError, LlmProviderError) as exc:
            session.rollback()
            items.append({"job_id": job_id, "result": "FAILED", "error": str(exc)})
    return response({"items": items})


@router.post("/jobs/{job_id}/decisions")
def decide(
    job_id: UUID, payload: DecisionRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    return response(create_decision(session, job_id, payload))


@router.post("/jobs/{job_id}/decisions/re-evaluate")
def reassess(
    job_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        create_decision(session, job_id, payload, reassessment_key=idempotency_key)
    )


@router.get("/decisions/{decision_id}")
def get_one(
    decision_id: UUID, session: Session = Depends(get_session)
) -> dict[str, object]:
    return response(get_decision(session, decision_id))


@router.get("/jobs/{job_id}/decisions")
def decision_history(
    job_id: UUID,
    strategy_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    items, total = list_decisions(session, job_id, strategy_id, page, page_size)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})
