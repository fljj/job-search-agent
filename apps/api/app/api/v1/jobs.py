from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.job import (
    BatchJobImportItem,
    BatchJobImportPayload,
    JobImportPayload,
    ParseRequest,
)
from apps.api.app.services.job_service import (
    get_job,
    get_parsed_detail,
    import_job,
    list_jobs,
    list_parsed_details,
    parse_job,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/import")
def import_one(payload: JobImportPayload,
               session: Session = Depends(get_session)) -> dict[str, object]:
    return response(import_job(session, payload))


@router.post("/import/batch")
def import_batch(payload: BatchJobImportPayload,
                 session: Session = Depends(get_session)) -> dict[str, object]:
    results: list[BatchJobImportItem] = []
    for index, item in enumerate(payload.items):
        try:
            imported = import_job(session, JobImportPayload.model_validate(item))
            results.append(BatchJobImportItem(index=index, result=imported.result, job=imported.job))
        except (ValueError, ValidationError) as exc:
            session.rollback()
            results.append(BatchJobImportItem(index=index, result="VALIDATION_FAILED", error=str(exc)))
    return response({"items": results})


@router.get("")
def list_all(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
             job_id: UUID | None = None,
             strategy_id: UUID | None = None, grade: str | None = None,
             eligibility: str | None = None, effective_job_status: str | None = None,
             work_mode: str | None = None, hard_rejected: bool | None = None,
             session: Session = Depends(get_session)) -> dict[str, object]:
    items, total = list_jobs(session, page, page_size, job_id, strategy_id, grade, eligibility,
                             effective_job_status, work_mode, hard_rejected)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})


@router.get("/{job_id}")
def get_one(job_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(get_job(session, job_id))


@router.post("/{job_id}/parse")
def parse(job_id: UUID, payload: ParseRequest,
          session: Session = Depends(get_session)) -> dict[str, object]:
    return response(parse_job(session, job_id, payload.mode))


@router.get("/{job_id}/parsed-details")
def parsed_history(
    job_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    items, total = list_parsed_details(session, job_id, page, page_size)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})


@router.get("/{job_id}/parsed-details/{parsed_detail_id}")
def parsed_detail(
    job_id: UUID,
    parsed_detail_id: UUID,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(get_parsed_detail(session, job_id, parsed_detail_id))
