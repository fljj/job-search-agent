from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.automation import AutomationDispatchRequest, AutomationSettingPayload
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
