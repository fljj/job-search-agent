from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.services.qualification_service import refresh_qualification
from packages.policy_engine.qualification import (
    QualificationContext,
    QualificationStatus,
)


@pytest.mark.parametrize(
    ("evaluated_evidence", "expected_status", "expected_evidence"),
    [
        (
            ["JOB_DIRECTION_CONFLICT"],
            QualificationStatus.ROUGH_MATCH,
            ["CONTACT_DECISION_SUPPORTS_DIRECTION"],
        ),
        (
            ["SALARY_CONFLICT"],
            QualificationStatus.MISMATCH,
            ["SALARY_CONFLICT"],
        ),
    ],
)
def test_contact_decision_only_prevents_weak_direction_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    evaluated_evidence: list[str],
    expected_status: QualificationStatus,
    expected_evidence: list[str],
) -> None:
    session = MagicMock(spec=Session)
    job = SimpleNamespace(id=uuid4())
    decision = SimpleNamespace(decision="CONTACT", hard_rejected=False)
    conversation = SimpleNamespace(
        job_id=job.id,
        latest_job_decision_id=uuid4(),
        strategy_id=uuid4(),
        qualification_status="UNKNOWN",
    )
    strategy = SimpleNamespace(id=conversation.strategy_id, priority=1)

    def get_entity(model: object, _entity_id: object) -> object | None:
        if model is db.Job:
            return job
        if model is db.JobDecision:
            return decision
        return None

    session.get.side_effect = get_entity
    monkeypatch.setattr(
        "apps.api.app.services.qualification_service._strategies",
        lambda *_args: [strategy],
    )
    monkeypatch.setattr(
        "apps.api.app.services.qualification_service._context",
        lambda *_args: QualificationContext(),
    )
    monkeypatch.setattr(
        "apps.api.app.services.qualification_service.evaluate_qualification",
        lambda _context: (QualificationStatus.MISMATCH, evaluated_evidence),
    )
    store = MagicMock(return_value=(expected_status, expected_evidence))
    monkeypatch.setattr(
        "apps.api.app.services.qualification_service._store",
        store,
    )

    result = refresh_qualification(session, conversation)  # type: ignore[arg-type]

    assert result == (expected_status, expected_evidence)
    assert store.call_args.args[2:4] == (expected_status, expected_evidence)
