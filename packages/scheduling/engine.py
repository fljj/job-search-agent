import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from packages.scheduling.models import (
    CalendarBusySlot,
    CalendarStatus,
    EventType,
    ParsedInvitation,
    SchedulingConfig,
)

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
CN_HOURS = {"九": 9, "十": 10, "十一": 11, "十二": 12, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}


def parse_invitation(text: str, received_at: datetime, config: SchedulingConfig) -> ParsedInvitation:
    zone = ZoneInfo(config.timezone)
    local_received = received_at.astimezone(zone)
    event_type = _event_type(text)
    if event_type is None:
        raise ValueError("SCHEDULING_INTENT_NOT_EXPLICIT")
    duration = _duration(event_type, config)
    target_date = _date(text, local_received.date())
    target_time = _time(text)
    risks: list[str] = []
    if target_date is None:
        risks.append("DATE_AMBIGUOUS")
    if target_time is None:
        risks.append("TIME_AMBIGUOUS")
    if not re.search(r"时区|北京时间|中国时间", text):
        risks.append("TIMEZONE_INFERRED")
    start = datetime.combine(target_date, target_time, zone) if target_date and target_time else None
    end = start + timedelta(minutes=duration) if start else None
    confidence = 0.95 if start else (0.7 if target_date else 0.45)
    return ParsedInvitation(
        event_type=event_type, start_at=start, end_at=end, timezone=config.timezone,
        duration_minutes=duration, source_text=text, confidence=confidence,
        risk_codes=risks,
    )


def check_calendar(invitation: ParsedInvitation, slots: list[CalendarBusySlot],
                   config: SchedulingConfig, calendar_available: bool = True) -> CalendarStatus:
    if not calendar_available:
        return CalendarStatus.UNAVAILABLE
    if invitation.start_at is None or invitation.end_at is None:
        return CalendarStatus.AMBIGUOUS
    if invitation.event_type is EventType.ONSITE_INTERVIEW and config.onsite_commute_minutes is None:
        return CalendarStatus.INCOMPLETE
    start, end = _protected_range(invitation.start_at, invitation.end_at, invitation.event_type, config)
    zone = ZoneInfo(config.timezone)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    workday_start = datetime.combine(local_start.date(), config.workday_start, zone)
    workday_end = datetime.combine(local_start.date(), config.workday_end, zone)
    lunch_start = datetime.combine(local_start.date(), config.lunch_start, zone)
    lunch_end = datetime.combine(local_start.date(), config.lunch_end, zone)
    if (
        local_start.weekday() >= 5
        or local_end.date() != local_start.date()
        or local_start < workday_start
        or local_end > workday_end
        or (local_start < lunch_end and local_end > lunch_start)
    ):
        return CalendarStatus.CONFLICT
    for slot in slots:
        if slot.availability in {"BUSY", "TENTATIVE", "OUT_OF_OFFICE"} and start < slot.end_at and end > slot.start_at:
            return CalendarStatus.CONFLICT
    return CalendarStatus.AVAILABLE


def suggest_slots(invitation: ParsedInvitation, slots: list[CalendarBusySlot],
                  config: SchedulingConfig) -> list[tuple[datetime, datetime]]:
    zone = ZoneInfo(config.timezone)
    day = invitation.start_at.astimezone(zone).date() if invitation.start_at else datetime.now(zone).date() + timedelta(days=1)
    result: list[tuple[datetime, datetime]] = []
    for offset in range(7):
        current = day + timedelta(days=offset)
        if current.weekday() >= 5:
            continue
        cursor = datetime.combine(current, config.workday_start, zone)
        end_work = datetime.combine(current, config.workday_end, zone)
        while cursor + timedelta(minutes=invitation.duration_minutes) <= end_work:
            end = cursor + timedelta(minutes=invitation.duration_minutes)
            lunch_start = datetime.combine(current, config.lunch_start, zone)
            lunch_end = datetime.combine(current, config.lunch_end, zone)
            candidate = invitation.model_copy(update={"start_at": cursor, "end_at": end})
            if not (cursor < lunch_end and end > lunch_start) and check_calendar(candidate, slots, config) is CalendarStatus.AVAILABLE:
                result.append((cursor, end))
                if len(result) == config.suggestion_count:
                    return result
            cursor += timedelta(minutes=30)
    return result


def _event_type(text: str) -> EventType | None:
    if "现场" in text or "到公司" in text:
        return EventType.ONSITE_INTERVIEW
    if "技术面" in text or "技术面试" in text:
        return EventType.TECHNICAL_INTERVIEW
    if "视频面" in text or "视频面试" in text:
        return EventType.VIDEO_INTERVIEW
    if any(term in text for term in ("电话", "通话", "语音")):
        return EventType.PHONE_CALL
    if "面试" in text:
        return EventType.TECHNICAL_INTERVIEW
    return None


def _duration(event_type: EventType, config: SchedulingConfig) -> int:
    return {EventType.PHONE_CALL: config.phone_duration_minutes,
            EventType.VIDEO_INTERVIEW: config.video_duration_minutes,
            EventType.TECHNICAL_INTERVIEW: config.technical_duration_minutes,
            EventType.ONSITE_INTERVIEW: config.onsite_duration_minutes}[event_type]


def _date(text: str, base: date) -> date | None:
    matched = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?", text)
    if matched:
        try:
            return date(*map(int, matched.groups()))
        except ValueError:
            return None
    matched = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if matched:
        try:
            return date(base.year, int(matched.group(1)), int(matched.group(2)))
        except ValueError:
            return None
    if "后天" in text:
        return base + timedelta(days=2)
    if "明天" in text:
        return base + timedelta(days=1)
    matched = re.search(r"(?:本周|下周|周|星期)([一二三四五六日天])", text)
    if matched:
        target = WEEKDAYS[matched.group(1)]
        days = (target - base.weekday()) % 7
        if "下周" in matched.group(0):
            days = days + 7 if days else 7
        return base + timedelta(days=days)
    return None


def _time(text: str) -> time | None:
    matched = re.search(r"(\d{1,2}):(\d{2})", text)
    if matched:
        try:
            return time(int(matched.group(1)), int(matched.group(2)))
        except ValueError:
            return None
    matched = re.search(r"(上午|下午|晚上)?([一二三四五六七八九十]{1,2})点(?:半|([0-5]?\d)分?)?", text)
    if not matched:
        return None
    hour = CN_HOURS.get(matched.group(2))
    if hour is None:
        return None
    if matched.group(1) in {"下午", "晚上"} and hour < 12:
        hour += 12
    minute = 30 if "半" in matched.group(0) else int(matched.group(3) or 0)
    return time(hour, minute)


def _protected_range(start: datetime, end: datetime, event_type: EventType,
                     config: SchedulingConfig) -> tuple[datetime, datetime]:
    commute = config.onsite_commute_minutes or 0 if event_type is EventType.ONSITE_INTERVIEW else 0
    return (start - timedelta(minutes=config.buffer_before_minutes + commute),
            end + timedelta(minutes=config.buffer_after_minutes + commute))
