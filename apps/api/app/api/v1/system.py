from fastapi import APIRouter

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.config import get_settings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/llm-status")
def llm_status() -> dict[str, object]:
    """返回不含密钥和端点凭证的 LLM 配置摘要。"""
    settings = get_settings()
    return response(
        {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "configured": settings.llm_configured,
        }
    )
