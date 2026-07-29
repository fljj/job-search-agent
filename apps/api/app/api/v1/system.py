from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.config import get_settings
from apps.api.app.core.database import get_session
from apps.api.app.services.llm_config_service import (
    llm_configuration,
    select_llm_configuration,
)

router = APIRouter(prefix="/system", tags=["system"])


class LlmSelectionPayload(BaseModel):
    provider: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=100)


@router.get("/llm-status")
def llm_status(session: Session = Depends(get_session)) -> dict[str, object]:
    """返回不含密钥和端点凭证的 LLM 配置摘要。"""
    return response(llm_configuration(session))


@router.put("/llm-status")
def save_llm_selection(
    payload: LlmSelectionPayload,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """保存当前模型选择；API Key 始终只从环境变量读取。"""
    return response(
        select_llm_configuration(session, payload.provider, payload.model)
    )


@router.get("/calendar-status")
def calendar_status() -> dict[str, object]:
    """返回不含 OAuth 凭证的日历配置摘要。"""
    settings = get_settings()
    calendar_id = (
        settings.apple_calendar_name
        if settings.calendar_provider == "APPLE"
        else settings.google_calendar_id
    )
    return response(
        {
            "provider": settings.calendar_provider,
            "calendar_id": calendar_id,
            "configured": settings.calendar_configured,
            "real_provider": settings.calendar_provider != "MOCK",
        }
    )
