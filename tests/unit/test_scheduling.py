from datetime import UTC, datetime

import pytest

from apps.api.app.schemas.scheduling import ApproveScheduleRequest
from packages.scheduling.engine import check_calendar, parse_invitation, suggest_slots
from packages.scheduling.models import CalendarBusySlot, CalendarStatus, EventType, SchedulingConfig


def test_parse_relative_phone_invitation_with_timezone_risk() -> None:
    parsed = parse_invitation(
        "明天上午十点可以电话聊一下吗", datetime(2026, 7, 21, 2, tzinfo=UTC),
        SchedulingConfig(),
    )
    assert parsed.event_type is EventType.PHONE_CALL
    assert parsed.start_at is not None
    assert parsed.start_at.isoformat() == "2026-07-22T10:00:00+08:00"
    assert parsed.duration_minutes == 30
    assert "TIMEZONE_INFERRED" in parsed.risk_codes


def test_explicit_technical_interview_uses_default_duration() -> None:
    parsed = parse_invitation(
        "2026-07-24 14:00 技术面试，北京时间", datetime.now(UTC), SchedulingConfig()
    )
    assert parsed.event_type is EventType.TECHNICAL_INTERVIEW
    assert parsed.duration_minutes == 90
    assert parsed.end_at is not None and parsed.start_at is not None
    assert (parsed.end_at - parsed.start_at).total_seconds() == 5400


def test_generic_chat_is_not_treated_as_phone_call() -> None:
    with pytest.raises(ValueError, match="SCHEDULING_INTENT_NOT_EXPLICIT"):
        parse_invitation(
            "你好，看过简历了，希望和你交流一下",
            datetime.now(UTC),
            SchedulingConfig(),
        )


def test_invalid_calendar_date_returns_ambiguous_result() -> None:
    parsed = parse_invitation(
        "2026-02-30 10:00 电话沟通",
        datetime.now(UTC),
        SchedulingConfig(),
    )

    assert parsed.start_at is None
    assert "DATE_AMBIGUOUS" in parsed.risk_codes


def test_ambiguous_time_never_claims_calendar_available() -> None:
    parsed = parse_invitation("明天方便视频面试吗", datetime(2026, 7, 21, tzinfo=UTC), SchedulingConfig())
    assert check_calendar(parsed, [], SchedulingConfig()) is CalendarStatus.AMBIGUOUS


def test_busy_tentative_and_buffers_conflict_but_free_does_not() -> None:
    config = SchedulingConfig()
    parsed = parse_invitation("2026-07-24 10:00 电话沟通", datetime.now(UTC), config)
    adjacent = CalendarBusySlot(
        start_at=datetime.fromisoformat("2026-07-24T09:30:00+08:00"),
        end_at=datetime.fromisoformat("2026-07-24T09:50:00+08:00"), availability="TENTATIVE",
    )
    assert check_calendar(parsed, [adjacent], config) is CalendarStatus.CONFLICT
    assert check_calendar(parsed, [adjacent.model_copy(update={"availability": "FREE"})], config) is CalendarStatus.AVAILABLE


def test_calendar_unavailable_safely_degrades() -> None:
    parsed = parse_invitation("2026-07-24 10:00 电话沟通", datetime.now(UTC), SchedulingConfig())
    assert check_calendar(parsed, [], SchedulingConfig(), calendar_available=False) is CalendarStatus.UNAVAILABLE


def test_onsite_without_commute_is_incomplete() -> None:
    parsed = parse_invitation("2026-07-24 10:00 到公司现场面试", datetime.now(UTC), SchedulingConfig())
    assert check_calendar(parsed, [], SchedulingConfig()) is CalendarStatus.INCOMPLETE


def test_outside_work_hours_lunch_and_weekend_are_conflicts() -> None:
    config = SchedulingConfig()
    for source in (
        "2026-07-24 08:00 电话沟通",
        "2026-07-24 12:30 电话沟通",
        "2026-07-25 10:00 电话沟通",
    ):
        parsed = parse_invitation(source, datetime.now(UTC), config)
        assert check_calendar(parsed, [], config) is CalendarStatus.CONFLICT


def test_schedule_selection_requires_a_valid_range() -> None:
    start = datetime.fromisoformat("2026-07-24T10:00:00+08:00")
    with pytest.raises(ValueError):
        ApproveScheduleRequest(
            reply_content="确认",
            selected_start_at=start,
        )
    with pytest.raises(ValueError):
        ApproveScheduleRequest(
            reply_content="确认",
            selected_start_at=start,
            selected_end_at=start,
        )
    with pytest.raises(ValueError, match="必须包含时区"):
        ApproveScheduleRequest(
            reply_content="确认",
            selected_start_at=datetime(2026, 7, 24, 10),
            selected_end_at=datetime(2026, 7, 24, 10, 30),
        )


def test_scheduling_config_rejects_invalid_workday_order() -> None:
    with pytest.raises(ValueError):
        SchedulingConfig(workday_end=datetime.min.time())


def test_conflict_produces_two_or_three_working_hour_candidates() -> None:
    config = SchedulingConfig()
    parsed = parse_invitation("2026-07-24 10:00 电话沟通", datetime.now(UTC), config)
    busy = CalendarBusySlot(
        start_at=datetime.fromisoformat("2026-07-24T09:00:00+08:00"),
        end_at=datetime.fromisoformat("2026-07-24T11:00:00+08:00"),
    )
    candidates = suggest_slots(parsed, [busy], config)
    assert len(candidates) in {2, 3}
    assert all(start.hour >= 9 and end.hour <= 18 for start, end in candidates)
