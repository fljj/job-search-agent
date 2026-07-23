from adapters.calendar.apple import AppleCalendarGateway
from adapters.calendar.google import GoogleCalendarGateway
from apps.api.app.core.config import Settings
from packages.scheduling.calendar import CalendarGateway


def build_calendar_gateway(settings: Settings) -> CalendarGateway | None:
    if settings.calendar_provider == "MOCK":
        return None
    if settings.calendar_provider == "APPLE":
        if not settings.calendar_configured:
            return None
        return AppleCalendarGateway(
            settings.apple_calendar_name,
            settings.calendar_timeout_seconds,
        )
    token = settings.google_calendar_access_token
    if token is None or not token.get_secret_value():
        return None
    return GoogleCalendarGateway(
        token.get_secret_value(),
        settings.google_calendar_id,
        settings.calendar_timeout_seconds,
    )
