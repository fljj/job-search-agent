import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from adapters.calendar.apple import AppleCalendarGateway
from packages.scheduling.calendar import CalendarProviderUnavailable


def gateway(tmp_path: Path) -> AppleCalendarGateway:
    script = tmp_path / "calendar.js"
    script.write_text("// fixture", encoding="utf-8")
    return AppleCalendarGateway("求职面试", script_path=script)


def test_apple_calendar_reads_busy_without_event_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("adapters.calendar.apple.sys.platform", "darwin")
    monkeypatch.setattr(
        "adapters.calendar.apple.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "busy": [
                        {
                            "start": "2026-07-25T02:00:00.000Z",
                            "end": "2026-07-25T03:00:00.000Z",
                        }
                    ],
                }
            ),
            stderr="",
        ),
    )
    slots = gateway(tmp_path).list_busy(
        datetime.fromisoformat("2026-07-25T09:00:00+08:00"),
        datetime.fromisoformat("2026-07-25T18:00:00+08:00"),
        "Asia/Shanghai",
    )
    assert len(slots) == 1
    assert slots[0].start_at.isoformat() == "2026-07-25T02:00:00+00:00"


def test_apple_event_creation_passes_values_as_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("adapters.calendar.apple.sys.platform", "darwin")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "event_id": "apple-event-1"}),
            stderr="",
        )

    monkeypatch.setattr("adapters.calendar.apple.subprocess.run", run)
    event_id = gateway(tmp_path).create_event(
        idempotency_key="schedule:one",
        title='面试"; Application("Finder")',
        start_at=datetime.fromisoformat("2026-07-25T10:00:00+08:00"),
        end_at=datetime.fromisoformat("2026-07-25T11:00:00+08:00"),
        timezone="Asia/Shanghai",
    )
    assert event_id == "apple-event-1"
    assert commands[0][0:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert '面试"; Application("Finder")' in commands[0]


def test_apple_permission_failure_is_calendar_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("adapters.calendar.apple.sys.platform", "darwin")
    monkeypatch.setattr(
        "adapters.calendar.apple.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "osascript")
        ),
    )
    with pytest.raises(CalendarProviderUnavailable):
        gateway(tmp_path).list_busy(
            datetime.fromisoformat("2026-07-25T09:00:00+08:00"),
            datetime.fromisoformat("2026-07-25T18:00:00+08:00"),
            "Asia/Shanghai",
        )
