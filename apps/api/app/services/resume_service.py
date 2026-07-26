from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import ResumePayload, ResumeResponse
from apps.api.app.services.errors import ResourceNotFoundError, VersionConflictError
from apps.api.app.services.job_service import get_job_entity
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.resume_selector.selector import ResumeCandidate, select_default_resume


def save_resume(session: Session, payload: ResumePayload, resume_id: object | None = None) -> ResumeResponse:
    ensure_default_user(session)
    resume = session.get(db.Resume, resume_id) if resume_id else None
    if resume_id and (resume is None or resume.user_id != DEFAULT_USER_ID):
        raise ResourceNotFoundError("简历不存在")
    if resume is None:
        if payload.version is not None:
            raise VersionConflictError("新建简历时不应提供 version")
        resume = db.Resume(user_id=DEFAULT_USER_ID)
        session.add(resume)
    elif payload.version != resume.version:
        raise VersionConflictError("简历版本已变化")
    else:
        resume.version += 1
    resume.platform = payload.platform
    resume.attachment_name = payload.attachment_name
    resume.target_directions = payload.target_directions
    resume.is_available = payload.is_available
    session.commit()
    session.refresh(resume)
    return _response(resume)


def list_resumes(session: Session, page: int, page_size: int) -> tuple[list[ResumeResponse], int]:
    query = select(db.Resume).where(db.Resume.user_id == DEFAULT_USER_ID)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(db.Resume.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return [_response(row) for row in rows], total


def select_resume_for_job(session: Session, job_id: object) -> ResumeResponse | None:
    get_job_entity(session, job_id)
    rows = session.scalars(
        select(db.Resume)
        .where(db.Resume.user_id == DEFAULT_USER_ID)
        .order_by(db.Resume.created_at.asc(), db.Resume.id.asc())
    ).all()
    selected = select_default_resume([
        ResumeCandidate(item.id, item.attachment_name, item.target_directions, item.is_available)
        for item in rows
    ])
    if selected is None:
        return None
    entity = next(item for item in rows if item.id == selected.id)
    return _response(entity)


def _response(resume: db.Resume) -> ResumeResponse:
    return ResumeResponse(id=resume.id, platform=resume.platform, attachment_name=resume.attachment_name,
                          target_directions=resume.target_directions, is_available=resume.is_available,
                          version=resume.version)
