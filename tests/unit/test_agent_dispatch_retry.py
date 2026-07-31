from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.services.agent_service import (
    _finish_retry_denied,
    _isolate_dispatch_error,
)


def test_retryable_dispatch_error_is_deferred_without_blocking_queue() -> None:
    session = MagicMock(spec=Session)
    current = datetime.now(UTC)
    run = db.AgentRun(id=uuid4(), platform="BOSS")
    draft = db.GeneratedDraft(id=uuid4(), draft_type="RESUME", content=None)
    action = db.ActionQueue(
        id=uuid4(),
        draft_id=draft.id,
        status="FAILED_RETRYABLE",
        failure_code="APPROVED_TARGET_PAGE_NOT_FOUND",
    )
    session.scalar.return_value = action

    should_pause = _isolate_dispatch_error(
        session,
        run,
        draft,
        current,
        ValueError("目标暂不可用"),
    )

    assert should_pause is False
    assert action.status == "FAILED_RETRYABLE"
    assert action.updated_at == current
    session.flush.assert_called_once()


def test_dispatch_error_after_execution_started_becomes_unknown() -> None:
    session = MagicMock(spec=Session)
    current = datetime.now(UTC)
    run = db.AgentRun(id=uuid4(), platform="BOSS")
    conversation = db.Conversation(id=uuid4(), state="ACTIVE")
    draft = db.GeneratedDraft(id=uuid4(), draft_type="RESUME", content=None)
    action = db.ActionQueue(
        id=uuid4(),
        conversation_id=conversation.id,
        draft_id=draft.id,
        status="EXECUTING",
    )
    session.scalar.return_value = action
    session.get.return_value = conversation

    should_pause = _isolate_dispatch_error(
        session,
        run,
        draft,
        current,
        ValueError("执行结果无法确认"),
    )

    assert should_pause is True
    assert action.status == "OUTCOME_UNKNOWN"
    assert action.failure_code == "DISPATCH_RESULT_UNKNOWN"
    assert conversation.state == "OUTCOME_UNKNOWN"
    session.flush.assert_called_once()


def test_retry_denied_by_current_policy_reaches_final_state() -> None:
    session = MagicMock(spec=Session)
    current = datetime.now(UTC)
    run = db.AgentRun(id=uuid4(), platform="BOSS")
    draft = db.GeneratedDraft(id=uuid4(), draft_type="REPLY", content="历史回复")
    action = db.ActionQueue(
        id=uuid4(),
        draft_id=draft.id,
        status="FAILED_RETRYABLE",
        failure_code="APPROVED_TARGET_PAGE_NOT_FOUND",
    )
    session.scalar.return_value = action

    _finish_retry_denied(
        session,
        run,
        draft,
        current,
        {"decision": "DENY", "reason_codes": ["CONVERSATION_ENDED"]},
    )

    assert action.status == "FAILED_FINAL"
    assert action.failure_code == "RETRY_POLICY_DENIED"
    assert action.finished_at == current
    session.flush.assert_called_once()
