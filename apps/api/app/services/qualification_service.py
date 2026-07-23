from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.services.user_service import DEFAULT_USER_ID
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
    strategy = _strategy(session, conversation)
    job = (
        session.get(db.Job, conversation.job_id)
        if conversation.job_id
        else None
    )
    if strategy is None:
        return _store(
            conversation,
            QualificationStatus.UNKNOWN,
            ["STRATEGY_NOT_BOUND"],
            message,
        )
    context = QualificationContext(
        company_name=job.company_name if job else None,
        job_title=job.title if job else None,
        industry=job.industry if job else None,
        location=job.location if job else None,
        work_mode=job.work_mode if job else None,
        salary_text=job.salary_text if job else None,
        description=job.description if job else None,
        message_text=message.content if message else "",
        accepted_directions=[
            item.pattern
            for item in (strategy.title_rules if strategy else [])
            if item.rule_type == "INCLUDE"
        ],
        excluded_industries=[
            item.industry_name
            for item in (strategy.industry_rules if strategy else [])
            if item.rule_type == "EXCLUDED"
        ],
        blacklisted_companies=[
            item.company_name for item in (strategy.blacklist if strategy else [])
        ],
        enabled_work_modes=[
            item.work_mode
            for item in (strategy.work_mode_rules if strategy else [])
            if item.enabled
        ],
        allowed_onsite_locations=[
            location.location_name
            for item in (strategy.work_mode_rules if strategy else [])
            if item.enabled and item.work_mode == "ONSITE"
            for location in item.locations
        ],
        minimum_salary_k=next(
            (
                float(item.minimum_monthly_k)
                for item in strategy.salary_rules
                if job
                and item.work_mode == job.work_mode
            ),
            None,
        ),
    )
    status, evidence = evaluate_qualification(context)
    if (
        conversation.qualification_status == QualificationStatus.MISMATCH.value
        and status is QualificationStatus.UNKNOWN
    ):
        status = QualificationStatus.MISMATCH
        evidence = conversation.qualification_evidence
    return _store(conversation, status, evidence, message)


def _store(
    conversation: db.Conversation,
    status: QualificationStatus,
    evidence: list[str],
    message: db.Message | None,
) -> tuple[QualificationStatus, list[str]]:
    changed = (
        conversation.qualification_status != status.value
        or conversation.qualification_evidence != evidence
    )
    conversation.qualification_status = status.value
    conversation.qualification_evidence = evidence
    if message and str(message.id) not in conversation.qualification_message_ids:
        conversation.qualification_message_ids = [
            *conversation.qualification_message_ids,
            str(message.id),
        ]
        changed = True
    if changed:
        conversation.qualification_version += 1
    return status, evidence


def qualification_response(
    session: Session, conversation_id: UUID
) -> dict[str, object]:
    conversation = session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ValueError("对话不存在")
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
        raise ValueError("对话不存在")
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


def _strategy(
    session: Session, conversation: db.Conversation
) -> db.JobStrategy | None:
    strategy = (
        session.get(db.JobStrategy, conversation.strategy_id)
        if conversation.strategy_id
        else None
    )
    if strategy is None:
        strategy = session.scalar(
            select(db.JobStrategy)
            .where(
                db.JobStrategy.user_id == DEFAULT_USER_ID,
                db.JobStrategy.enabled.is_(True),
            )
            .order_by(db.JobStrategy.priority.asc(), db.JobStrategy.created_at.asc())
            .limit(1)
        )
    if strategy is not None and conversation.strategy_id is None:
        conversation.strategy_id = strategy.id
    return strategy
