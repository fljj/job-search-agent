from apps.api.app.models import entities as db
from apps.api.app.services.action_service import (
    _retry_policy_denied_after_prewrite_failure,
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
