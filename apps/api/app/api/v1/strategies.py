from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.strategy import StrategyPayload
from apps.api.app.services.strategy_service import (
    create_strategy,
    get_strategy,
    list_strategies,
    set_status,
    update_strategy,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


class StatusPayload(BaseModel):
    enabled: bool
    version: int = Field(ge=1)


@router.post("")
def create(payload: StrategyPayload, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_strategy(session, payload))


@router.get("")
def list_all(enabled: bool | None = None, page: int = Query(1, ge=1),
             page_size: int = Query(20, ge=1, le=100),
             session: Session = Depends(get_session)) -> dict[str, object]:
    items, total = list_strategies(session, enabled, page, page_size)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})


@router.get("/{strategy_id}")
def get_one(strategy_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(get_strategy(session, strategy_id))


@router.put("/{strategy_id}")
def update(strategy_id: UUID, payload: StrategyPayload,
           session: Session = Depends(get_session)) -> dict[str, object]:
    return response(update_strategy(session, strategy_id, payload))


@router.patch("/{strategy_id}/status")
def update_status(strategy_id: UUID, payload: StatusPayload,
                  session: Session = Depends(get_session)) -> dict[str, object]:
    return response(set_status(session, strategy_id, payload.enabled, payload.version))
