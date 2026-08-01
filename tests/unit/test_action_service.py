from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.services.action_service import (
    _ensure_telegram_conversation,
    _retry_policy_denied_after_prewrite_failure,
)


def test_successful_telegram_greeting_creates_message_center_conversation() -> None:
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    action = db.ActionQueue(
        id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        job_id=uuid4(),
        action_type="GREETING",
        platform="TELEGRAM",
        target_company="CoinMarketCap",
        target_job_title="Java Developer",
        target_recruiter="@EvanSun0212",
        target_conversation_key=None,
    )

    def assign_conversation_id() -> None:
        conversation = next(
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], db.Conversation)
        )
        conversation.id = uuid4()

    session.flush.side_effect = assign_conversation_id

    conversation = _ensure_telegram_conversation(session, action)

    assert conversation is not None
    assert conversation.external_conversation_id == "telegram:evansun0212"
    assert conversation.state == "ACTIVE"
    assert action.conversation_id == conversation.id
    assert any(
        isinstance(call.args[0], db.AuditEvent)
        and call.args[0].event_type == "TELEGRAM_CONVERSATION_STARTED"
        for call in session.add.call_args_list
    )


def test_user_can_retry_policy_denial_only_after_proven_prewrite_failure() -> None:
    action = db.ActionQueue(
        failure_code="RETRY_POLICY_DENIED",
        write_started_at=None,
    )
    attempt = db.ActionAttempt(
        error_code="APPROVED_TARGET_PAGE_NOT_FOUND",
        write_started=False,
    )

    assert _retry_policy_denied_after_prewrite_failure(action, attempt)

    attempt.write_started = True
    assert not _retry_policy_denied_after_prewrite_failure(action, attempt)
