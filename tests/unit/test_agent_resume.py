from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.services.agent_service import resume_run
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.policy_engine.automation import AutomationRules


def paused_run() -> db.AgentRun:
    return db.AgentRun(
        id=uuid4(),
        user_id=DEFAULT_USER_ID,
        strategy_id=uuid4(),
        platform="BOSS",
        status="PAUSED",
        pause_reason_codes=["RESULT_NOT_OBSERVED"],
        version=1,
    )


def test_result_not_observed_cannot_resume_before_reconciliation() -> None:
    session = MagicMock(spec=Session)
    run = paused_run()
    session.get.return_value = run
    session.scalar.return_value = uuid4()

    with pytest.raises(ValueError, match="仍有发送结果待对账"):
        resume_run(session, run.id)

    assert run.status == "PAUSED"
    session.commit.assert_not_called()


def test_reconciled_result_can_clear_platform_pause_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    run = paused_run()
    platform_setting = db.AutomationSetting(
        user_id=DEFAULT_USER_ID,
        scope_type="PLATFORM",
        scope_key="BOSS",
        enabled=True,
        paused=True,
    )
    session.get.return_value = run
    session.scalar.side_effect = [None, platform_setting]
    monkeypatch.setattr(
        "apps.api.app.services.agent_service._effective_rules",
        lambda *_: AutomationRules(enabled=True),
    )

    result = resume_run(session, run.id)

    assert result["status"] == "RUNNING"
    assert run.pause_reason_codes == []
    assert platform_setting.paused is False
    session.commit.assert_called_once()


def test_message_discovery_reconnect_resets_only_failure_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    run = db.AgentRun(
        id=uuid4(),
        user_id=DEFAULT_USER_ID,
        strategy_id=uuid4(),
        platform="LIEPIN",
        status="PAUSED",
        pause_reason_codes=["MESSAGE_DISCOVERY_UNAVAILABLE"],
        cursor={
            "message_discovery": {"scroll_position": 10},
            "message_discovery_health": {"consecutive_failure_count": 3},
        },
        version=1,
    )
    session.get.return_value = run
    monkeypatch.setattr(
        "apps.api.app.services.agent_service._effective_rules",
        lambda *_: AutomationRules(enabled=True),
    )

    result = resume_run(session, run.id)

    assert result["status"] == "RUNNING"
    assert run.cursor == {"message_discovery": {"scroll_position": 10}}
    session.commit.assert_called_once()
