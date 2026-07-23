from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from adapters.llm.errors import LlmProviderError
from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.score import BatchScoreRequest, ScoreRequest
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.score_service import create_score, get_score, list_scores

router = APIRouter(tags=["scores"])


@router.post("/jobs/scores/batch")
def score_batch(
    payload: BatchScoreRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for job_id in payload.job_ids:
        try:
            result = create_score(
                session,
                job_id,
                ScoreRequest(
                    strategy_id=payload.strategy_id,
                    candidate_profile_id=payload.candidate_profile_id,
                ),
            )
            items.append({"job_id": job_id, "result": "SCORED", "score": result})
        except (ValueError, ResourceNotFoundError, LlmProviderError) as exc:
            session.rollback()
            items.append({"job_id": job_id, "result": "FAILED", "error": str(exc)})
    return response({"items": items})


@router.post("/jobs/{job_id}/scores")
def score(job_id: UUID, payload: ScoreRequest,
          session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_score(session, job_id, payload))


@router.post("/jobs/{job_id}/scores/re-evaluate")
def reassess(
    job_id: UUID,
    payload: ScoreRequest,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        create_score(
            session,
            job_id,
            payload,
            reassessment_key=idempotency_key,
        )
    )


@router.get("/scores/{score_id}")
def get_one(score_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(get_score(session, score_id))


@router.get("/jobs/{job_id}/scores")
def score_history(
    job_id: UUID,
    strategy_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    items, total = list_scores(session, job_id, strategy_id, page, page_size)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})
