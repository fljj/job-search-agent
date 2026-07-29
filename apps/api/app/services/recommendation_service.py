import hashlib
import uuid
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.browser.maimai_recommendations import (
    MaimaiRecommendationAdapter,
    MaimaiRecommendationCard,
)
from apps.api.app.core.recommendation_config import get_recommendation_rules
from apps.api.app.models import entities as db
from apps.api.app.services.action_service import execute_action, reconcile_action
from apps.api.app.services.automation_service import _effective_rules
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.actions import ActionExecutor
from packages.policy_engine.automation import AutomationRules
from packages.policy_engine.recommendation import (
    RecommendationDecision,
    RecommendationRules,
    decide_recommendation,
)
from packages.policy_engine.state_machine import ActionStatus

POLICY_VERSION = "platform-recommendation-v1"


class RecommendationScanner(Protocol):
    def scan(
        self,
        cdp_url: str,
        rules: RecommendationRules,
        limit: int = 20,
    ) -> list[MaimaiRecommendationCard]: ...


def scan_recommendations(
    session: Session,
    run: db.AgentRun,
    cdp_url: str,
    *,
    adapter: RecommendationScanner | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    if run.user_id != DEFAULT_USER_ID or run.platform != "MAIMAI":
        raise ValueError("推荐扫描仅支持当前用户的脉脉运行")
    automation = _effective_rules(session, "MAIMAI", run.strategy_id)
    if (
        not automation.enabled
        or automation.paused
        or automation.emergency_stop
        or not automation.maimai_recommendation_enabled
    ):
        raise ValueError("脉脉系统推荐自动化未启用")
    strategy = session.get(db.JobStrategy, run.strategy_id)
    if strategy is None or not strategy.enabled:
        raise ValueError("求职策略不存在或未启用")
    rows = session.scalars(
        select(db.PlatformRecommendation).where(
            db.PlatformRecommendation.user_id == DEFAULT_USER_ID,
            db.PlatformRecommendation.agent_run_id == run.id,
            db.PlatformRecommendation.platform == "MAIMAI",
            db.PlatformRecommendation.status == "DECIDED",
            db.PlatformRecommendation.action_id.is_(None),
        )
    ).all()
    for row in rows:
        _authorize_existing_recommendation(
            session, row, run, strategy, automation
        )
    cards = (adapter or MaimaiRecommendationAdapter()).scan(
        cdp_url, get_recommendation_rules(), limit
    )
    observed_rows = [
        _persist_card(session, run, strategy, automation, card) for card in cards
    ]
    rows_by_id = {row.id: row for row in [*rows, *observed_rows]}
    session.commit()
    return [
        _response(row, _action_or_none(session, row.action_id))
        for row in rows_by_id.values()
    ]


def list_recommendations(
    session: Session,
    *,
    platform: str | None = None,
    decision: str | None = None,
    status: str | None = None,
) -> list[dict[str, object]]:
    query = select(db.PlatformRecommendation).where(
        db.PlatformRecommendation.user_id == DEFAULT_USER_ID
    )
    if platform:
        query = query.where(db.PlatformRecommendation.platform == platform)
    if decision:
        query = query.where(db.PlatformRecommendation.decision == decision)
    if status:
        query = query.where(db.PlatformRecommendation.status == status)
    rows = session.scalars(
        query.order_by(db.PlatformRecommendation.last_observed_at.desc())
    ).all()
    return [
        _response(row, _action_or_none(session, row.action_id))
        for row in rows
    ]


def get_recommendation(session: Session, recommendation_id: UUID) -> dict[str, object]:
    row = _required(session, recommendation_id)
    return _response(row, _action_or_none(session, row.action_id))


def _action_or_none(
    session: Session, action_id: UUID | None
) -> db.ActionQueue | None:
    return session.get(db.ActionQueue, action_id) if action_id is not None else None


def dispatch_recommendation(
    session: Session,
    recommendation_id: UUID,
    cdp_url: str,
    *,
    executor: ActionExecutor | None = None,
) -> dict[str, object]:
    row = _required(session, recommendation_id)
    if row.action_id is None:
        raise ValueError("该推荐未获得自动执行授权")
    action = session.get(db.ActionQueue, row.action_id)
    if action is None:
        raise RuntimeError("推荐关联动作不存在")
    before = row.status
    result = execute_action(session, action.id, cdp_url, executor)
    _sync_status(row, result.status, result.observed_content)
    if before not in {"ACCEPTED", "REJECTED"} and row.status in {
        "ACCEPTED",
        "REJECTED",
    }:
        _audit_completion(session, row, action)
    session.commit()
    return _response(row, action)


def reconcile_recommendation(
    session: Session,
    recommendation_id: UUID,
    cdp_url: str,
) -> dict[str, object]:
    row = _required(session, recommendation_id)
    if row.action_id is None:
        raise ValueError("该推荐没有待对账动作")
    result = reconcile_action(session, row.action_id, cdp_url)
    action = session.get(db.ActionQueue, row.action_id)
    _sync_status(row, result.status, result.observed_content)
    session.commit()
    return _response(row, action)


def _persist_card(
    session: Session,
    run: db.AgentRun,
    strategy: db.JobStrategy,
    automation: AutomationRules,
    card: MaimaiRecommendationCard,
) -> db.PlatformRecommendation:
    existing = session.scalar(
        select(db.PlatformRecommendation).where(
            db.PlatformRecommendation.user_id == DEFAULT_USER_ID,
            db.PlatformRecommendation.platform == "MAIMAI",
            db.PlatformRecommendation.external_recommendation_id
            == card.external_recommendation_id,
        )
    )
    if existing:
        existing.last_observed_at = datetime.now(UTC)
        _authorize_existing_recommendation(
            session, existing, run, strategy, automation
        )
        return existing
    decision, reasons = decide_recommendation(
        recruiter=card.recruiter_name,
        company=card.company_name,
        job_title=card.job_title,
        card_text=card.card_text,
        rules=get_recommendation_rules(),
        blacklisted_companies=[item.company_name for item in strategy.blacklist],
    )
    row = db.PlatformRecommendation(
        user_id=DEFAULT_USER_ID,
        agent_run_id=run.id,
        platform="MAIMAI",
        external_recommendation_id=card.external_recommendation_id,
        recruiter_name=card.recruiter_name,
        company_name=card.company_name,
        job_title=card.job_title,
        location=card.location,
        salary_text=card.salary_text,
        description_summary=card.description_summary,
        card_hash=card.card_hash,
        decision=decision.value,
        reason_codes=reasons,
        status="DENIED" if decision is RecommendationDecision.DENY else "DECIDED",
    )
    session.add(row)
    session.flush()
    action_type = (
        "PLATFORM_RECOMMENDATION_ACCEPT"
        if decision is RecommendationDecision.ACCEPT_AND_SEND_PROFILE
        else "PLATFORM_RECOMMENDATION_REJECT"
    )
    enabled = decision is not RecommendationDecision.DENY
    if decision is RecommendationDecision.ACCEPT_AND_SEND_PROFILE:
        enabled = enabled and automation.maimai_recommendation_resume_enabled
        if not enabled:
            row.reason_codes = [*reasons, "RECOMMENDATION_RESUME_DISABLED"]
    if enabled:
        action = _create_action(session, row, run, strategy, action_type)
        row.action_id = action.id
    _audit(session, row)
    return row


def _authorize_existing_recommendation(
    session: Session,
    row: db.PlatformRecommendation,
    run: db.AgentRun,
    strategy: db.JobStrategy,
    automation: AutomationRules,
) -> None:
    if row.action_id is not None or row.status != "DECIDED":
        return
    try:
        decision = RecommendationDecision(row.decision)
    except ValueError:
        return
    if decision is RecommendationDecision.DENY:
        return
    action_type = (
        "PLATFORM_RECOMMENDATION_ACCEPT"
        if decision is RecommendationDecision.ACCEPT_AND_SEND_PROFILE
        else "PLATFORM_RECOMMENDATION_REJECT"
    )
    if (
        decision is RecommendationDecision.ACCEPT_AND_SEND_PROFILE
        and not automation.maimai_recommendation_resume_enabled
    ):
        return
    action = _create_action(session, row, run, strategy, action_type)
    row.action_id = action.id
    _audit(session, row)


def _create_action(
    session: Session,
    row: db.PlatformRecommendation,
    run: db.AgentRun,
    strategy: db.JobStrategy,
    action_type: str,
) -> db.ActionQueue:
    identity = f"MAIMAI:{row.external_recommendation_id}:{action_type}"
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID,
        strategy_id=strategy.id,
        agent_run_id=run.id,
        authorization_source="AUTO",
        action_type=action_type,
        status=ActionStatus.APPROVED.value,
        content=row.description_summary,
        delivery_mode="PLATFORM_DEFAULT",
        platform="MAIMAI",
        target_company=row.company_name,
        target_job_title=row.job_title,
        target_recruiter=row.recruiter_name,
        target_conversation_key=row.external_recommendation_id,
        idempotency_key=identity,
        send_fingerprint=fingerprint,
        approved_at=datetime.now(UTC),
    )
    session.add(action)
    session.flush()
    return action


def _sync_status(
    row: db.PlatformRecommendation,
    action_status: str,
    observed_content: str | None,
) -> None:
    if action_status == "SUCCEEDED":
        row.status = (
            "ACCEPTED"
            if row.decision == RecommendationDecision.ACCEPT_AND_SEND_PROFILE.value
            else "REJECTED"
        )
    else:
        row.status = action_status
    row.observed_result = observed_content
    row.last_observed_at = datetime.now(UTC)


def _required(session: Session, recommendation_id: UUID) -> db.PlatformRecommendation:
    row = session.get(db.PlatformRecommendation, recommendation_id)
    if row is None or row.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("平台推荐不存在")
    return row


def _audit(session: Session, row: db.PlatformRecommendation) -> None:
    session.add(
        db.AuditEvent(
            user_id=DEFAULT_USER_ID,
            actor_type="SYSTEM",
            event_type="PLATFORM_RECOMMENDATION_DECIDED",
            entity_type="platform_recommendation",
            entity_id=row.id,
            before_state=None,
            after_state=row.status,
            reason_codes=row.reason_codes,
            metadata_json={
                "platform": row.platform,
                "policy_version": POLICY_VERSION,
                "card_hash": row.card_hash,
                "decision": row.decision,
            },
            correlation_id=f"recommendation:{row.id}:{uuid.uuid4()}",
        )
    )


def _audit_completion(
    session: Session,
    row: db.PlatformRecommendation,
    action: db.ActionQueue,
) -> None:
    event_types = (
        ["PLATFORM_RECOMMENDATION_ACCEPTED", "PLATFORM_PROFILE_SENT"]
        if row.status == "ACCEPTED"
        else ["PLATFORM_RECOMMENDATION_REJECTED"]
    )
    for event_type in event_types:
        session.add(
            db.AuditEvent(
                user_id=DEFAULT_USER_ID,
                actor_type="SYSTEM",
                event_type=event_type,
                entity_type="platform_recommendation",
                entity_id=row.id,
                before_state="DECIDED",
                after_state=row.status,
                reason_codes=[],
                metadata_json={
                    "platform": row.platform,
                    "action_id": str(action.id),
                    "evidence_hash": hashlib.sha256(
                        (row.observed_result or "").encode()
                    ).hexdigest(),
                },
                correlation_id=f"recommendation:{row.id}:{action.id}",
            )
        )


def _response(
    row: db.PlatformRecommendation,
    action: db.ActionQueue | None,
) -> dict[str, object]:
    return {
        "id": row.id,
        "platform": row.platform,
        "external_recommendation_id": row.external_recommendation_id,
        "recruiter_name": row.recruiter_name,
        "company_name": row.company_name,
        "job_title": row.job_title,
        "location": row.location,
        "salary_text": row.salary_text,
        "description_summary": row.description_summary,
        "decision": row.decision,
        "reason_codes": row.reason_codes,
        "status": row.status,
        "action_id": row.action_id,
        "action_status": action.status if action else None,
        "failure_code": action.failure_code if action else None,
        "observed_result": row.observed_result,
        "first_observed_at": row.first_observed_at,
        "last_observed_at": row.last_observed_at,
    }
