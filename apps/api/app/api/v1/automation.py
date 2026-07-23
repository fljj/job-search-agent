from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.automation import (
    AgentRunStartRequest,
    AgentRunTickRequest,
    AutomationDispatchRequest,
    AutomationSettingPayload,
)
from apps.api.app.services.agent_service import (
    get_run,
    pause_run,
    resume_run,
    start_run,
    tick_run,
)
from apps.api.app.services.automation_service import dispatch, list_settings, upsert_setting

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/settings")
def settings(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_settings(session)})


@router.put("/settings")
def save_setting(payload: AutomationSettingPayload,
                 session: Session = Depends(get_session)) -> dict[str, object]:
    return response(upsert_setting(session, payload))


@router.post("/dispatch")
def run(payload: AutomationDispatchRequest,
        session: Session = Depends(get_session)) -> dict[str, object]:
    return response(dispatch(session, payload))


@router.post("/runs")
def start(payload: AgentRunStartRequest,
          session: Session = Depends(get_session)) -> dict[str, object]:
    return response(start_run(session, payload))


@router.get("/runs/{run_id}")
def get_one(run_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(get_run(session, run_id))


@router.post("/runs/{run_id}/pause")
def pause(run_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(pause_run(session, run_id))


@router.post("/runs/{run_id}/resume")
def resume(run_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(resume_run(session, run_id))


@router.post("/runs/{run_id}/tick")
def tick(
    run_id: UUID,
    payload: AgentRunTickRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(tick_run(session, run_id, payload.worker_id))
