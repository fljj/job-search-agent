import json
from datetime import datetime
from typing import cast
from urllib.error import URLError
from urllib.request import Request

import pytest

from adapters.calendar.google import GoogleCalendarGateway
from packages.scheduling.calendar import CalendarProviderUnavailable


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_google_freebusy_returns_busy_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "adapters.calendar.google.urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "calendars": {
                    "primary": {
                        "busy": [
                            {
                                "start": "2026-07-25T02:00:00Z",
                                "end": "2026-07-25T03:00:00Z",
                            }
                        ]
                    }
                }
            }
        ),
    )
    gateway = GoogleCalendarGateway("test-token")
    slots = gateway.list_busy(
        datetime.fromisoformat("2026-07-25T09:00:00+08:00"),
        datetime.fromisoformat("2026-07-25T18:00:00+08:00"),
        "Asia/Shanghai",
    )
    assert len(slots) == 1
    assert slots[0].start_at.isoformat() == "2026-07-25T02:00:00+00:00"


def test_google_event_uses_stable_idempotent_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_open(request: Request, **_kwargs: object) -> FakeResponse:
        data = json.loads(cast(bytes, request.data or b"{}"))
        captured.append(data)
        return FakeResponse({"id": data["id"]})

    monkeypatch.setattr("adapters.calendar.google.urlopen", fake_open)
    gateway = GoogleCalendarGateway("test-token")
    def create() -> str:
        return gateway.create_event(
            idempotency_key="schedule:one",
            title="视频面试",
            start_at=datetime.fromisoformat("2026-07-25T10:00:00+08:00"),
            end_at=datetime.fromisoformat("2026-07-25T11:00:00+08:00"),
            timezone="Asia/Shanghai",
        )

    first = create()
    second = create()
    assert first == second
    assert captured[0]["id"] == captured[1]["id"]


def test_google_calendar_failure_is_safe_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "adapters.calendar.google.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(CalendarProviderUnavailable):
        GoogleCalendarGateway("test-token").list_busy(
            datetime.fromisoformat("2026-07-25T09:00:00+08:00"),
            datetime.fromisoformat("2026-07-25T18:00:00+08:00"),
            "Asia/Shanghai",
        )
