from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.v1.helpers import response
from apps.api.app.core.database import get_session
from apps.api.app.schemas.automation import (
    AgentRunStartRequest,
    AgentRunTickRequest,
    AutomationDispatchRequest,
    AutomationSettingPayload,
)
from apps.api.app.schemas.rollout import RolloutCreateRequest, RolloutTransitionRequest
from apps.api.app.services.agent_service import (
    get_run,
    list_runs,
    pause_run,
    resume_run,
    start_run,
    tick_run,
)
from apps.api.app.services.automation_service import (
    dispatch,
    list_automatic_actions,
    list_settings,
    upsert_setting,
)
from apps.api.app.services.operations_service import (
    audit_discrepancies,
    list_reconciliation_tasks,
    operations_status,
    process_reconciliation_queue,
    verify_successful_actions,
)
from apps.api.app.services.rollout_service import (
    get_or_create_rollout,
    list_rollouts,
    rollout_status,
    transition_rollout,
)

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/settings")
def settings(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_settings(session)})


@router.put("/settings")
def save_setting(payload: AutomationSettingPayload,
                 session: Session = Depends(get_session)) -> dict[str, object]:
    return response(upsert_setting(session, payload))


@router.get("/rollouts")
def rollouts(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_rollouts(session)})


@router.put("/rollouts")
def save_rollout(
    payload: RolloutCreateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(get_or_create_rollout(session, payload))


@router.get("/rollouts/{platform}")
def get_rollout(
    platform: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return response(rollout_status(session, platform))


@router.post("/rollouts/{platform}/transition")
def transition(
    platform: str,
    payload: RolloutTransitionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(transition_rollout(session, platform, payload))


@router.post("/dispatch")
def run(payload: AutomationDispatchRequest,
        session: Session = Depends(get_session)) -> dict[str, object]:
    return response(dispatch(session, payload))


@router.post("/runs")
def start(payload: AgentRunStartRequest,
          session: Session = Depends(get_session)) -> dict[str, object]:
    return response(start_run(session, payload))


@router.get("/runs")
def runs(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_runs(session)})


@router.get("/actions")
def actions(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": list_automatic_actions(session)})


@router.get("/runs/{run_id}")
def get_one(run_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(get_run(session, run_id))


@router.post("/runs/{run_id}/pause")
def pause(run_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(pause_run(session, run_id))


@router.post("/runs/{run_id}/resume")
def resume(run_id: UUID, session: Session = Depends(get_session)) -> dict[str, object]:
    return response(resume_run(session, run_id))


@router.post("/runs/{run_id}/tick")
def tick(
    run_id: UUID,
    payload: AgentRunTickRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(tick_run(session, run_id, payload.worker_id))


@router.get("/operations/status")
def operation_status(session: Session = Depends(get_session)) -> dict[str, object]:
    return response(operations_status(session))


@router.get("/operations/reconciliation")
def reconciliation_tasks(
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response({"items": list_reconciliation_tasks(session)})


@router.post("/operations/reconciliation/run")
def run_reconciliation(
    cdp_url: str = "http://127.0.0.1:9222",
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(process_reconciliation_queue(session, cdp_url))


@router.get("/operations/discrepancies")
def discrepancies(session: Session = Depends(get_session)) -> dict[str, object]:
    return response({"items": audit_discrepancies(session)})


@router.post("/operations/audit/run")
def run_audit(
    cdp_url: str = "http://127.0.0.1:9222",
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return response(
        {"items": verify_successful_actions(session, cdp_url)}
    )
