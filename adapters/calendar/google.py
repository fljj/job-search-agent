import hashlib
import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from packages.scheduling.calendar import CalendarProviderUnavailable
from packages.scheduling.models import CalendarBusySlot


class GoogleCalendarGateway:
    """Google Calendar v3 的最小忙闲查询和幂等事件写入适配器。"""

    provider = "GOOGLE"

    def __init__(
        self,
        access_token: str,
        calendar_id: str = "primary",
        timeout_seconds: int = 10,
    ) -> None:
        self.access_token = access_token
        self.calendar_id = calendar_id
        self.timeout_seconds = timeout_seconds

    def list_busy(
        self, start_at: datetime, end_at: datetime, timezone: str
    ) -> list[CalendarBusySlot]:
        payload = self._request(
            "https://www.googleapis.com/calendar/v3/freeBusy",
            {
                "timeMin": start_at.isoformat(),
                "timeMax": end_at.isoformat(),
                "timeZone": timezone,
                "items": [{"id": self.calendar_id}],
            },
        )
        calendars = payload.get("calendars")
        if not isinstance(calendars, dict):
            raise CalendarProviderUnavailable("Google Calendar 返回缺少日历数据")
        calendar = calendars.get(self.calendar_id)
        if not isinstance(calendar, dict):
            raise CalendarProviderUnavailable("Google Calendar 返回缺少目标日历")
        if calendar.get("errors"):
            raise CalendarProviderUnavailable("Google Calendar 忙闲查询失败")
        return [
            CalendarBusySlot(
                start_at=datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00")),
                end_at=datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00")),
            )
            for item in calendar.get("busy", [])
        ]

    def create_event(
        self,
        *,
        idempotency_key: str,
        title: str,
        start_at: datetime,
        end_at: datetime,
        timezone: str,
    ) -> str:
        event_id = hashlib.sha256(idempotency_key.encode()).hexdigest()
        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{quote(self.calendar_id, safe='')}/events"
        )
        try:
            payload = self._request(
                url,
                {
                    "id": event_id,
                    "summary": title,
                    "start": {"dateTime": start_at.isoformat(), "timeZone": timezone},
                    "end": {"dateTime": end_at.isoformat(), "timeZone": timezone},
                },
            )
        except CalendarProviderUnavailable as exc:
            if exc.__cause__ and isinstance(exc.__cause__, HTTPError):
                if exc.__cause__.code == 409:
                    return event_id
            raise
        return str(payload.get("id") or event_id)

    def _request(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CalendarProviderUnavailable(
                "Google Calendar 当前不可用"
            ) from exc
        if not isinstance(result, dict):
            raise CalendarProviderUnavailable("Google Calendar 返回格式无效")
        return result
