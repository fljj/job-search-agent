from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.llm import build_llm_provider
from apps.api.app.models import entities as db
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.llm.ports import LlmProvider


def runtime_settings(
    session: Session,
    settings: Settings | None = None,
) -> Settings:
    base = settings or get_settings()
    selected = session.scalar(
        select(db.LlmRuntimeSetting).where(
            db.LlmRuntimeSetting.user_id == DEFAULT_USER_ID
        )
    )
    provider = (
        selected.provider
        if selected is not None
        else (base.available_llm_providers[0] if base.available_llm_providers else "")
    )
    if not provider:
        return base
    model = (
        selected.model
        if selected is not None
        else base.configured_model_for(provider)
    )
    return base.with_llm_selection(provider, model)


def build_runtime_llm_provider(
    session: Session,
    settings: Settings | None = None,
) -> LlmProvider:
    return build_llm_provider(runtime_settings(session, settings))


def llm_configuration(session: Session) -> dict[str, object]:
    base = get_settings()
    selected = runtime_settings(session, base)
    options = []
    for provider in base.available_llm_providers:
        configured = base.with_llm_selection(
            provider, base.configured_model_for(provider)
        )
        options.append(
            {
                "provider": provider,
                "model": configured.llm_model,
                "configured": configured.llm_configured,
            }
        )
    return {
        "provider": selected.llm_provider,
        "model": selected.llm_model,
        "configured": selected.llm_configured,
        "options": options,
    }


def select_llm_configuration(
    session: Session,
    provider: str,
    model: str,
) -> dict[str, object]:
    base = get_settings()
    normalized = provider.upper()
    if normalized not in base.available_llm_providers:
        raise ValueError("该 LLM 供应商未在环境配置中启用")
    expected_model = base.configured_model_for(normalized)
    if model != expected_model:
        raise ValueError("该模型未在环境配置中启用")
    selected = base.with_llm_selection(normalized, model)
    if not selected.llm_configured:
        raise ValueError("该模型尚未配置 API Key")
    ensure_default_user(session)
    row = session.scalar(
        select(db.LlmRuntimeSetting).where(
            db.LlmRuntimeSetting.user_id == DEFAULT_USER_ID
        )
    )
    if row is None:
        row = db.LlmRuntimeSetting(
            user_id=DEFAULT_USER_ID,
            provider=normalized,
            model=model,
        )
        session.add(row)
    else:
        row.provider = normalized
        row.model = model
        row.version += 1
    circuit = session.scalar(
        select(db.LlmCircuitBreaker).where(
            db.LlmCircuitBreaker.user_id == DEFAULT_USER_ID
        )
    )
    if circuit is not None:
        circuit.provider = normalized
        circuit.model = model
        circuit.status = "CLOSED"
        circuit.failure_code = None
        circuit.probe_attempt_count = 0
        circuit.next_probe_at = None
    session.commit()
    return llm_configuration(session)
