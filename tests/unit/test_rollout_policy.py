import pytest

from apps.api.app.services.rollout_service import requires_job_score
from packages.policy_engine.rollout import RolloutLevel, action_limit, allows_job_scan


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (RolloutLevel.MESSAGE_READ_ONLY, False),
        (RolloutLevel.JOB_READ_ONLY, True),
        (RolloutLevel.FORMAL_LIMITS, True),
    ],
)
def test_job_scan_starts_at_second_level(level: int, expected: bool) -> None:
    assert allows_job_scan(level) is expected


@pytest.mark.parametrize(
    ("level", "action_type", "expected"),
    [
        (1, "REPLY", 0),
        (2, "GREETING", 0),
        (3, "REPLY", 5),
        (3, "LOW_SCORE_DECLINE", 5),
        (3, "MISMATCH_DECLINE", 5),
        (3, "GREETING", 0),
        (4, "GREETING", 3),
        (4, "RESUME", 0),
        (5, "RESUME", 50),
        (4, "PLATFORM_RECOMMENDATION_ACCEPT", 0),
        (5, "PLATFORM_RECOMMENDATION_ACCEPT", 50),
        (5, "PLATFORM_RECOMMENDATION_REJECT", 50),
        (6, "REPLY", 50),
        (6, "GREETING", 50),
    ],
)
def test_action_limits_follow_rollout_order(
    level: int, action_type: str, expected: int
) -> None:
    assert action_limit(
        level,
        action_type,
        reply_daily_limit=5,
        greeting_daily_limit=3,
        formal_daily_limit=50,
    ) == expected


def test_unknown_action_is_always_denied() -> None:
    assert (
        action_limit(
            RolloutLevel.FORMAL_LIMITS,
            "UNKNOWN",
            reply_daily_limit=5,
            greeting_daily_limit=3,
            formal_daily_limit=50,
        )
        == 0
    )


@pytest.mark.parametrize(
    ("action_type", "expected"),
    [
        ("GREETING", True),
        ("REPLY", False),
        ("RESUME", False),
        ("MISMATCH_DECLINE", False),
        ("PLATFORM_RECOMMENDATION_ACCEPT", False),
    ],
)
def test_only_proactive_greeting_requires_formal_job_score(
    action_type: str, expected: bool
) -> None:
    assert requires_job_score(action_type) is expected
