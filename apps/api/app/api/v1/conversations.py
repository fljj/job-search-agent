from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.conversation import (
    ConversationPayload,
    GreetingRequest,
    MessagePayload,
    ReplyRequest,
)
from apps.api.app.services.conversation_service import (
    create_conversation,
    create_greeting_draft,
    create_reply_draft,
    import_message,
    list_confirmation_tasks,
)

router = APIRouter(tags=["conversations"])


@router.post("/conversations")
def create(payload: ConversationPayload, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_conversation(session, payload))


@router.post("/conversations/{conversation_id}/messages")
def add_message(conversation_id: UUID, payload: MessagePayload,
                session: Session = Depends(get_session)) -> dict[str, object]:
    return response(import_message(session, conversation_id, payload))


@router.post("/drafts/reply")
def reply(payload: ReplyRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_reply_draft(session, payload.message_id))


@router.post("/drafts/greeting")
def greeting(payload: GreetingRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_greeting_draft(session, payload.job_score_id))


@router.get("/confirmation-tasks")
def confirmation_tasks(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_confirmation_tasks(session)})
