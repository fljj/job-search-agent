from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.conversation import (
    ConversationPayload,
    DraftEditRequest,
    GreetingRequest,
    MessagePayload,
    ReplyRequest,
    ResumeDraftRequest,
)
from apps.api.app.services.conversation_service import (
    create_conversation,
    create_greeting_draft,
    create_reply_draft,
    create_resume_draft,
    edit_draft,
    import_message,
    list_conversations,
)
from apps.api.app.services.qualification_service import (
    evaluate_conversation_qualification,
    qualification_response,
)

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
def list_all(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_conversations(session)})


@router.post("/conversations")
def create(payload: ConversationPayload, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_conversation(session, payload))


@router.post("/conversations/{conversation_id}/messages")
def add_message(conversation_id: UUID, payload: MessagePayload,
                session: Session = Depends(get_session)) -> dict[str, object]:
    return response(import_message(session, conversation_id, payload))


@router.get("/conversations/{conversation_id}/qualification")
def qualification(
    conversation_id: UUID,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(qualification_response(session, conversation_id))


@router.post("/conversations/{conversation_id}/qualification/evaluate")
def evaluate_qualification(
    conversation_id: UUID,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        evaluate_conversation_qualification(session, conversation_id)
    )


@router.post("/drafts/reply")
def reply(payload: ReplyRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_reply_draft(session, payload.message_id))


@router.post("/drafts/greeting")
def greeting(payload: GreetingRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_greeting_draft(session, payload.job_score_id))


@router.post("/drafts/resume")
def resume(payload: ResumeDraftRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(create_resume_draft(session, payload.message_id))


@router.patch("/drafts/{draft_id}")
def edit(
    draft_id: UUID,
    payload: DraftEditRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(edit_draft(session, draft_id, payload.content))
