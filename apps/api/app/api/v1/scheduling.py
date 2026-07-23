from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.calendar import build_calendar_gateway
from apps.api.app.core.config import get_settings
from apps.api.app.core.database import get_session
from apps.api.app.schemas.scheduling import (
    AnalyzeInvitationRequest,
    ApproveScheduleRequest,
    CalendarEventPayload,
    ExecuteScheduleRequest,
    SchedulingPreferencePayload,
)
from apps.api.app.services.scheduling_service import (
    analyze_invitation,
    approve_schedule,
    execute_schedule,
    get_preference,
    import_calendar_event,
    list_requests,
    reject_schedule,
    save_preference,
)

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


@router.get("/settings")
def settings(session: Session = Depends(get_session)) -> dict[str, object]:
    return response(get_preference(session))


@router.put("/settings")
def update_settings(payload: SchedulingPreferencePayload,
                    session: Session = Depends(get_session)) -> dict[str, object]:
    return response(save_preference(session, payload))


@router.post("/calendar-events")
def add_calendar_event(payload: CalendarEventPayload,
                       session: Session = Depends(get_session)) -> dict[str, object]:
    return response(import_calendar_event(session, payload))


@router.post("/analyze")
def analyze(payload: AnalyzeInvitationRequest,
            session: Session = Depends(get_session)) -> dict[str, object]:
    gateway = build_calendar_gateway(get_settings())
    available = (
        payload.calendar_available
        if get_settings().calendar_provider == "MOCK"
        else gateway is not None
    )
    return response(
        analyze_invitation(session, payload.message_id, available, gateway)
    )


@router.get("/requests")
def requests(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_requests(session)})


@router.post("/requests/{request_id}/approve")
def approve(request_id: UUID, payload: ApproveScheduleRequest,
            session: Session = Depends(get_session)) -> dict[str, object]:
    return response(approve_schedule(session, request_id, payload))


@router.post("/requests/{request_id}/execute")
def execute(request_id: UUID, payload: ExecuteScheduleRequest,
            session: Session = Depends(get_session)) -> dict[str, object]:
    settings = get_settings()
    gateway = build_calendar_gateway(settings)
    return response(
        execute_schedule(
            session,
            request_id,
            payload.cdp_url,
            gateway,
            settings.calendar_provider == "MOCK" or gateway is not None,
        )
    )


@router.post("/requests/{request_id}/reject")
def reject(request_id: UUID,
           session: Session = Depends(get_session)) -> dict[str, object]:
    return response(reject_schedule(session, request_id))
