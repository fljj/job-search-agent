from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.conversation import KnowledgeItemPayload
from apps.api.app.services.knowledge_service import list_knowledge_items, save_knowledge_item

router = APIRouter(prefix="/knowledge-items", tags=["knowledge"])


@router.post("")
def create(payload: KnowledgeItemPayload, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(save_knowledge_item(session, payload))


@router.put("/{item_id}")
def update(item_id: UUID, payload: KnowledgeItemPayload,
           session: Session = Depends(get_session)) -> dict[str, object]:
    return response(save_knowledge_item(session, payload, item_id))


@router.get("")
def list_all(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
             session: Session = Depends(get_session)) -> dict[str, object]:
    items, total = list_knowledge_items(session, page, page_size)
    return response({"items": items, "page": page, "page_size": page_size, "total": total})
