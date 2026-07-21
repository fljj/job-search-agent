from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.browser import BrowserReadRequest
from apps.api.app.services.browser_service import list_platform_sessions, read_current_page

router = APIRouter(prefix="/browser", tags=["browser-readonly"])


@router.post("/read-current")
def read_current(payload: BrowserReadRequest,
                 session: Session = Depends(get_session)) -> dict[str, object]:
    return response(read_current_page(session, payload))


@router.get("/sessions")
def sessions(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_platform_sessions(session)})
