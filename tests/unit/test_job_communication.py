from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

from apps.api.app.models import entities as db
from apps.api.app.services.job_service import (
    _build_communication_summary,
    _communication_summary,
)


def test_job_communication_exposes_retryable_greeting_failure() -> None:
    session = MagicMock()
    action_id = uuid4()
    session.scalar.side_effect = [
        None,
        SimpleNamespace(
            id=action_id,
            status="FAILED_RETRYABLE",
            failure_code="APPROVED_TARGET_PAGE_NOT_FOUND",
        ),
        SimpleNamespace(reason_codes=["PREWRITE_GREETING_RETRY"]),
    ]

    result = _communication_summary(
        session,
        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        SimpleNamespace(automation_eligible=True),  # type: ignore[arg-type]
    )

    assert result == {
        "status": "GREETING_RETRY_PENDING",
        "conversation_id": None,
        "action_id": action_id,
        "action_status": "FAILED_RETRYABLE",
        "failure_code": "APPROVED_TARGET_PAGE_NOT_FOUND",
        "reason_codes": ["PREWRITE_GREETING_RETRY"],
    }


def test_job_communication_links_existing_conversation() -> None:
    session = MagicMock()
    conversation_id = uuid4()
    session.scalar.side_effect = [
        SimpleNamespace(id=conversation_id),
        None,
        None,
    ]

    result = _communication_summary(
        session,
        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        None,
    )

    assert result["status"] == "CONVERSATION_ACTIVE"
    assert result["conversation_id"] == conversation_id


def test_skipped_discovery_is_not_presented_as_ready_to_contact() -> None:
    decision_id = uuid4()
    result = _build_communication_summary(
        cast(db.JobDecision, SimpleNamespace(
            id=decision_id, automation_eligible=True, rejection_reasons=[]
        )),
        None,
        None,
        cast(
            db.JobDiscoveryRecord,
            SimpleNamespace(
                status="SKIPPED", reason_codes=["WORK_MODE_UNKNOWN"],
                job_decision_id=decision_id,
            ),
        ),
    )

    assert result["status"] == "NOT_CONTACTED"
    assert result["reason_codes"] == ["WORK_MODE_UNKNOWN"]


def test_job_communication_ignores_stale_discovery_reason_after_reassessment() -> None:
    result = _build_communication_summary(
        cast(db.JobDecision, SimpleNamespace(
            id=uuid4(), automation_eligible=False,
            rejection_reasons=[{"rule_code": "SALARY_BELOW_CONTACT_THRESHOLD"}],
        )),
        None,
        None,
        cast(db.JobDiscoveryRecord, SimpleNamespace(
            status="SKIPPED", reason_codes=["ONSITE_LOCATION_NOT_ALLOWED"],
            job_decision_id=uuid4(),
        )),
    )

    assert result["status"] == "NOT_CONTACTED"
    assert result["reason_codes"] == ["SALARY_BELOW_CONTACT_THRESHOLD"]
