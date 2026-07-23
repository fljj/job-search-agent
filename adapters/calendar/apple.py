import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from packages.scheduling.calendar import CalendarProviderUnavailable
from packages.scheduling.models import CalendarBusySlot


class AppleCalendarGateway:
    """通过 macOS Calendar JXA 接口读取忙闲并创建已授权事件。"""

    provider = "APPLE"

    def __init__(
        self,
        calendar_name: str,
        timeout_seconds: int = 10,
        script_path: Path | None = None,
    ) -> None:
        self.calendar_name = calendar_name
        self.timeout_seconds = timeout_seconds
        self.script_path = script_path or Path(__file__).with_name("apple_calendar.js")

    def list_busy(
        self, start_at: datetime, end_at: datetime, timezone: str
    ) -> list[CalendarBusySlot]:
        del timezone
        payload = self._run("list_busy", start_at.isoformat(), end_at.isoformat())
        slots = payload.get("busy")
        if not isinstance(slots, list):
            raise CalendarProviderUnavailable("Apple Calendar 返回缺少忙闲数据")
        return [
            CalendarBusySlot(
                start_at=datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00")),
                end_at=datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00")),
            )
            for item in slots
            if isinstance(item, dict) and item.get("start") and item.get("end")
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
        del timezone
        stable_id = hashlib.sha256(idempotency_key.encode()).hexdigest()
        payload = self._run(
            "create_event",
            stable_id,
            title,
            start_at.isoformat(),
            end_at.isoformat(),
        )
        external_id = payload.get("event_id")
        if not external_id:
            raise CalendarProviderUnavailable("Apple Calendar 未返回事件标识")
        return str(external_id)

    def _run(self, action: str, *arguments: str) -> dict[str, object]:
        if sys.platform != "darwin":
            raise CalendarProviderUnavailable("Apple Calendar 仅支持 macOS")
        if not self.script_path.is_file():
            raise CalendarProviderUnavailable("Apple Calendar 调用脚本不存在")
        try:
            result = subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-l",
                    "JavaScript",
                    str(self.script_path),
                    action,
                    self.calendar_name,
                    *arguments,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            payload = json.loads(result.stdout)
        except (
            OSError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
        ) as exc:
            raise CalendarProviderUnavailable(
                "Apple Calendar 当前不可用，请检查 macOS 日历自动化权限"
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise CalendarProviderUnavailable(
                str(payload.get("error") if isinstance(payload, dict) else "")
                or "Apple Calendar 返回格式无效"
            )
        return payload
