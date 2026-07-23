from datetime import datetime
from typing import Protocol

from packages.scheduling.models import CalendarBusySlot


class CalendarProviderUnavailable(RuntimeError):
    pass


class CalendarGateway(Protocol):
    """真实日历边界；读取忙闲与写事件使用独立方法。"""

    provider: str

    def list_busy(
        self, start_at: datetime, end_at: datetime, timezone: str
    ) -> list[CalendarBusySlot]: ...

    def create_event(
        self,
        *,
        idempotency_key: str,
        title: str,
        start_at: datetime,
        end_at: datetime,
        timezone: str,
    ) -> str: ...
