from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.core.conversation_config import get_conversation_policy
from apps.api.app.models import entities as db
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.job_matching.work_mode import infer_effective_work_mode
from packages.policy_engine.qualification import (
    QualificationContext,
    QualificationStatus,
    evaluate_qualification,
)


def refresh_qualification(
    session: Session,
    conversation: db.Conversation,
    *,
    message: db.Message | None = None,
) -> tuple[QualificationStatus, list[str]]:
    job = (
        session.get(db.Job, conversation.job_id)
        if conversation.job_id
        else None
    )
    strategies = _strategies(session, conversation)
    if not strategies:
        return _store(
            session,
            conversation,
            QualificationStatus.UNKNOWN,
            ["STRATEGY_NOT_BOUND"],
            message,
        )
    candidates = [
        (*evaluate_qualification(_context(strategy, conversation, job, message)), strategy)
        for strategy in strategies
    ]
    rank = {
        QualificationStatus.FULL_MATCH: 3,
        QualificationStatus.ROUGH_MATCH: 2,
        QualificationStatus.UNKNOWN: 1,
        QualificationStatus.MISMATCH: 0,
    }
    status, evidence, strategy = max(
        candidates,
        key=lambda item: (rank[item[0]], -item[2].priority),
    )
    if (
        conversation.strategy_id is None
        or conversation.qualification_status
        in {
            QualificationStatus.UNKNOWN.value,
            QualificationStatus.MISMATCH.value,
        }
    ):
        conversation.strategy_id = strategy.id
    if (
        conversation.qualification_status == QualificationStatus.MISMATCH.value
        and status is QualificationStatus.UNKNOWN
        and not (conversation.recruiter_role == "HEADHUNTER" and job is None)
    ):
        status = QualificationStatus.MISMATCH
        evidence = conversation.qualification_evidence
        message = None
    return _store(session, conversation, status, evidence, message)


def _context(
    strategy: db.JobStrategy,
    conversation: db.Conversation,
    job: db.Job | None,
    message: db.Message | None,
) -> QualificationContext:
    policy = get_conversation_policy()
    work_mode = (
        infer_effective_work_mode(
            job.work_mode,
            title=job.title,
            description=job.description,
            location=job.location,
        ).value
        if job
        else None
    )
    mode_rules = [
        item
        for item in strategy.work_mode_rules
        if item.enabled and (work_mode is None or item.work_mode == work_mode)
    ]
    return QualificationContext(
        company_name=(
            job.company_name if job else conversation.observed_company_name
        ),
        job_title=(
            job.title
            if job
            else (
                None
                if conversation.recruiter_role == "HEADHUNTER"
                else conversation.observed_job_title
            )
        ),
        industry=job.industry if job else None,
        location=job.location if job else None,
        work_mode=work_mode,
        salary_text=job.salary_text if job else None,
        description=job.description if job else None,
        message_text=message.content if message else "",
        accepted_directions=[
            item.pattern
            for item in strategy.title_rules
            if item.rule_type == "INCLUDE"
        ],
        excluded_industries=[
            item.industry_name
            for item in strategy.industry_rules
            if item.rule_type == "EXCLUDED"
        ],
        blacklisted_companies=[
            item.company_name for item in strategy.blacklist
        ],
        enabled_work_modes=[
            item.work_mode
            for item in strategy.work_mode_rules
            if item.enabled
        ],
        allowed_locations=[
            location.location_name
            for item in mode_rules
            for location in item.locations
        ],
        salary_threshold_k=_salary_threshold(strategy, work_mode),
        prohibited_direction_keywords=policy.prohibited_direction_keywords,
        related_direction_keywords=policy.related_direction_keywords,
    )


def _salary_threshold(
    strategy: db.JobStrategy,
    work_mode: str | None,
) -> float | None:
    exact = next(
        (
            float(item.expected_monthly_k)
            for item in strategy.salary_rules
            if work_mode and item.work_mode == work_mode
        ),
        None,
    )
    if exact is not None:
        return exact
    if work_mode not in {None, "UNKNOWN", ""}:
        return None
    enabled_modes = {
        item.work_mode for item in strategy.work_mode_rules if item.enabled
    }
    candidates = [
        float(item.expected_monthly_k)
        for item in strategy.salary_rules
        if item.work_mode in enabled_modes
    ]
    return min(candidates) if candidates else None


def _store(
    session: Session,
    conversation: db.Conversation,
    status: QualificationStatus,
    evidence: list[str],
    message: db.Message | None,
) -> tuple[QualificationStatus, list[str]]:
    before_status = conversation.qualification_status
    before_evidence = conversation.qualification_evidence
    before_message_ids = conversation.qualification_message_ids
    message_ids = [str(message.id)] if message else before_message_ids
    changed = (
        before_status != status.value
        or before_evidence != evidence
        or before_message_ids != message_ids
    )
    conversation.qualification_status = status.value
    conversation.qualification_evidence = evidence
    conversation.qualification_message_ids = message_ids
    if changed:
        conversation.qualification_version += 1
        session.add(
            db.AuditEvent(
                user_id=DEFAULT_USER_ID,
                actor_type="SYSTEM",
                event_type="QUALIFICATION_CHANGED",
                entity_type="conversation",
                entity_id=conversation.id,
                before_state=before_status,
                after_state=status.value,
                reason_codes=evidence,
                metadata_json={
                    "before_evidence": before_evidence,
                    "evidence_message_ids": message_ids,
                    "version": conversation.qualification_version,
                },
                correlation_id=f"qualification:{conversation.id}",
            )
        )
    return status, evidence


def qualification_response(
    session: Session, conversation_id: UUID
) -> dict[str, object]:
    conversation = session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    return {
        "conversation_id": conversation.id,
        "status": conversation.qualification_status,
        "evidence": conversation.qualification_evidence,
        "message_ids": conversation.qualification_message_ids,
        "version": conversation.qualification_version,
    }


def evaluate_conversation_qualification(
    session: Session, conversation_id: UUID
) -> dict[str, object]:
    conversation = session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    message = session.scalar(
        select(db.Message)
        .where(
            db.Message.conversation_id == conversation.id,
            db.Message.direction == "INBOUND",
        )
        .order_by(db.Message.received_at.desc())
        .limit(1)
    )
    refresh_qualification(session, conversation, message=message)
    session.commit()
    return qualification_response(session, conversation.id)


def _strategies(
    session: Session, conversation: db.Conversation
) -> list[db.JobStrategy]:
    strategy = (
        session.get(db.JobStrategy, conversation.strategy_id)
        if conversation.strategy_id
        else None
    )
    if (
        strategy is not None
        and strategy.enabled
        and conversation.qualification_status
        not in {
            QualificationStatus.UNKNOWN.value,
            QualificationStatus.MISMATCH.value,
        }
    ):
        return [strategy]
    return list(
        session.scalars(
            select(db.JobStrategy)
            .where(
                db.JobStrategy.user_id == DEFAULT_USER_ID,
                db.JobStrategy.enabled.is_(True),
            )
            .order_by(db.JobStrategy.priority.asc(), db.JobStrategy.created_at.asc())
        ).all()
    )
