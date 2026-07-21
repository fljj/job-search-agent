from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import KnowledgeItemPayload, KnowledgeItemResponse
from apps.api.app.services.errors import ResourceNotFoundError, VersionConflictError
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.job_parser.normalizers import normalize_text


def save_knowledge_item(
    session: Session, payload: KnowledgeItemPayload, item_id: object | None = None
) -> KnowledgeItemResponse:
    ensure_default_user(session)
    item = session.get(db.KnowledgeItem, item_id) if item_id else None
    if item_id and (item is None or item.user_id != DEFAULT_USER_ID):
        raise ResourceNotFoundError("知识项不存在")
    if item is None:
        if payload.version is not None:
            raise VersionConflictError("新建知识项时不应提供 version")
        item = db.KnowledgeItem(user_id=DEFAULT_USER_ID)
        session.add(item)
    elif payload.version != item.version:
        raise VersionConflictError("知识项版本已变化")
    else:
        item.version += 1
    item.category = payload.category
    item.key = payload.key
    item.normalized_key = normalize_text(payload.key)
    item.fact = payload.fact
    item.source = payload.source
    item.allowed_for_auto_reply = payload.allowed_for_auto_reply
    item.sensitivity = payload.sensitivity.value
    item.verified_at = payload.verified_at
    item.valid_until = payload.valid_until
    session.commit()
    session.refresh(item)
    return knowledge_response(item)


def list_knowledge_items(session: Session, page: int, page_size: int) -> tuple[list[KnowledgeItemResponse], int]:
    query = select(db.KnowledgeItem).where(db.KnowledgeItem.user_id == DEFAULT_USER_ID)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(db.KnowledgeItem.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return [knowledge_response(row) for row in rows], total


def get_knowledge_entities(session: Session) -> list[db.KnowledgeItem]:
    return list(session.scalars(select(db.KnowledgeItem).where(db.KnowledgeItem.user_id == DEFAULT_USER_ID)).all())


def knowledge_response(item: db.KnowledgeItem) -> KnowledgeItemResponse:
    return KnowledgeItemResponse(id=item.id, category=item.category, key=item.key, fact=item.fact,
                                 source=item.source, allowed_for_auto_reply=item.allowed_for_auto_reply,
                                 sensitivity=item.sensitivity, verified_at=item.verified_at,
                                 valid_until=item.valid_until, version=item.version)
