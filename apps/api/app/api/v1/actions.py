from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.action import (
    ApproveRequest,
    ExecuteRequest,
    GreetingConfirmationRequest,
    ModifyRequest,
    ReconcileRequest,
    ResumeConfirmationRequest,
)
from apps.api.app.services.action_service import (
    approve_retry,
    approve_task,
    create_greeting_confirmation,
    create_resume_confirmation,
    execute_action,
    list_tasks,
    modify_task,
    reconcile_action,
    reject_task,
)

router = APIRouter(tags=["manual-actions"])


@router.get("/confirmation-tasks")
def tasks(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_tasks(session)})


@router.post("/confirmation-tasks/resume")
def resume_task(
    payload: ResumeConfirmationRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    return response(
        {"id": create_resume_confirmation(session, payload.conversation_id, payload.resume_id)}
    )


@router.post("/confirmation-tasks/greeting")
def greeting_task(
    payload: GreetingConfirmationRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        {
            "id": create_greeting_confirmation(
                session,
                payload.draft_id,
                payload.recruiter_name,
            )
        }
    )


@router.post("/confirmation-tasks/{task_id}/approve")
def approve(
    task_id: UUID,
    payload: ApproveRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(approve_task(session, task_id, payload.conversation_id, idempotency_key))


@router.post("/confirmation-tasks/{task_id}/modify")
def modify(
    task_id: UUID, payload: ModifyRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    return response({"id": modify_task(session, task_id, payload.content)})


@router.post("/confirmation-tasks/{task_id}/reject")
def reject(task_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    reject_task(session, task_id)
    return response({"status": "CANCELLED"})


@router.post("/actions/{action_id}/execute")
def execute(
    action_id: UUID, payload: ExecuteRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    return response(execute_action(session, action_id, payload.cdp_url))


@router.post("/actions/{action_id}/retry")
def retry(action_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(approve_retry(session, action_id))


@router.post("/actions/{action_id}/reconcile")
def reconcile(
    action_id: UUID,
    payload: ReconcileRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(reconcile_action(session, action_id, payload.cdp_url))
