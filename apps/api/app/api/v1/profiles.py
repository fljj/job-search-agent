from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.profile import ProfilePayload
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.profile_service import get_profile, save_profile

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("")
def read_profile(session: Session = Depends(get_session)) -> dict[str, object]:
    profile = get_profile(session)
    if profile is None:
        raise ResourceNotFoundError("候选人资料不存在")
    return response(profile)


@router.put("")
def write_profile(payload: ProfilePayload, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(save_profile(session, payload))
