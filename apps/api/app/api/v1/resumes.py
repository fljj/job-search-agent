from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.conversation import ResumePayload
from apps.api.app.services.resume_service import list_resumes, save_resume, select_resume_for_job

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.get("/select")
def select_for_job(job_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(select_resume_for_job(session, job_id))


@router.post("")
def create(payload: ResumePayload, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(save_resume(session, payload))


@router.put("/{resume_id}")
def update(resume_id: UUID, payload: ResumePayload,
           session: Session = Depends(get_session)) -> dict[str, object]:
    return response(save_resume(session, payload, resume_id))


@router.get("")
def list_all(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
             session: Session = Depends(get_session)) -> dict[str, object]:
    items, total = list_resumes(session, page, page_size)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})
