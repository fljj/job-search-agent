from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.llm.errors import LlmProviderError
from apps.api.app.core.config import Settings
from apps.api.app.models import entities as db
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.llm.ports import LlmProvider

LLM_OUTAGE_CODES = {
    "LLM_NOT_CONFIGURED",
    "LLM_AUTHENTICATION_FAILED",
    "LLM_RATE_LIMITED",
    "LLM_TIMEOUT",
    "LLM_NETWORK_ERROR",
    "LLM_SERVICE_ERROR",
}
PROBE_DELAYS_SECONDS = (300, 600, 1200, 2400, 3600)


def llm_circuit_status(
    session: Session,
    settings: Settings,
) -> dict[str, object]:
    row = session.scalar(
        select(db.LlmCircuitBreaker).where(
            db.LlmCircuitBreaker.user_id == DEFAULT_USER_ID
        )
    )
    if row is None:
        return {
            "status": "CLOSED",
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "failure_code": None,
            "probe_attempt_count": 0,
            "opened_at": None,
            "last_probe_at": None,
            "next_probe_at": None,
            "recovered_at": None,
        }
    return _response(row)


def llm_circuit_is_open(session: Session) -> bool:
    status = session.scalar(
        select(db.LlmCircuitBreaker.status).where(
            db.LlmCircuitBreaker.user_id == DEFAULT_USER_ID
        )
    )
    return status in {"OPEN", "PROBING"}


def open_llm_circuit(
    session: Session,
    settings: Settings,
    failure_code: str,
    *,
    now: datetime | None = None,
) -> bool:
    if failure_code not in LLM_OUTAGE_CODES:
        return False
    current = now or datetime.now(UTC)
    row = _locked_row(session, settings)
    if row.status == "CLOSED":
        row.status = "OPEN"
        row.opened_at = current
        row.probe_attempt_count = 0
        row.next_probe_at = current + timedelta(
            seconds=PROBE_DELAYS_SECONDS[0]
        )
        row.recovered_at = None
        row.failure_code = failure_code
    # 熔断期间的派生错误不能覆盖首次真实故障，否则限流或网络异常会被
    # 后续“Provider 暂不可用”误报成未配置。探测请求仍可更新故障原因。
    row.provider = settings.llm_provider
    row.model = settings.llm_model
    session.flush()
    return True


def probe_llm_circuit(
    session: Session,
    settings: Settings,
    provider: LlmProvider,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now(UTC)
    row = _locked_row(session, settings)
    if row.status == "CLOSED":
        session.commit()
        return _response(row)
    if row.status == "PROBING":
        stale_after = timedelta(seconds=settings.llm_timeout_seconds + 60)
        if row.last_probe_at and current - row.last_probe_at <= stale_after:
            session.commit()
            return _response(row)
        # 探测进程可能在请求期间异常退出，超时后允许下一轮自动接管。
        row.status = "OPEN"
    if not force and row.next_probe_at and row.next_probe_at > current:
        session.commit()
        return _response(row)
    row.status = "PROBING"
    row.last_probe_at = current
    session.commit()

    try:
        provider.health_check()
    except LlmProviderError as exc:
        row = _locked_row(session, settings)
        row.status = "OPEN"
        row.failure_code = exc.code
        row.probe_attempt_count += 1
        row.next_probe_at = current + timedelta(
            seconds=_probe_delay_seconds(row.probe_attempt_count)
        )
        session.commit()
        return _response(row)

    row = _locked_row(session, settings)
    row.status = "CLOSED"
    row.failure_code = None
    row.probe_attempt_count = 0
    row.next_probe_at = None
    row.recovered_at = current
    session.commit()
    return _response(row)


def _locked_row(
    session: Session,
    settings: Settings,
) -> db.LlmCircuitBreaker:
    ensure_default_user(session)
    row = session.scalar(
        select(db.LlmCircuitBreaker)
        .where(db.LlmCircuitBreaker.user_id == DEFAULT_USER_ID)
        .with_for_update()
    )
    if row is not None:
        return row
    row = db.LlmCircuitBreaker(
        user_id=DEFAULT_USER_ID,
        provider=settings.llm_provider,
        model=settings.llm_model,
        status="CLOSED",
    )
    session.add(row)
    session.flush()
    return row


def _response(row: db.LlmCircuitBreaker) -> dict[str, object]:
    return {
        "status": row.status,
        "provider": row.provider,
        "model": row.model,
        "failure_code": row.failure_code,
        "probe_attempt_count": row.probe_attempt_count,
        "opened_at": row.opened_at.isoformat() if row.opened_at else None,
        "last_probe_at": (
            row.last_probe_at.isoformat() if row.last_probe_at else None
        ),
        "next_probe_at": (
            row.next_probe_at.isoformat() if row.next_probe_at else None
        ),
        "recovered_at": (
            row.recovered_at.isoformat() if row.recovered_at else None
        ),
    }


def _probe_delay_seconds(attempt_count: int) -> int:
    return PROBE_DELAYS_SECONDS[
        min(max(attempt_count, 0), len(PROBE_DELAYS_SECONDS) - 1)
    ]
