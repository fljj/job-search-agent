from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from adapters.browser.message_discovery import (
    DiscoveredConversation,
    MessageDiscoveryBatch,
)
from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import ConversationPayload, MessagePayload
from apps.api.app.schemas.job import JobImportPayload
from apps.api.app.services.conversation_service import (
    create_conversation,
    import_message,
)
from apps.api.app.services.job_service import import_job
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.models import MessageDirection

TERMINAL_CONVERSATION_STATES = {
    "ENDED",
    "DECLINED",
    "PAUSED",
    "OUTCOME_UNKNOWN",
}


def persist_discovery_batch(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    batch: MessageDiscoveryBatch,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    if run.platform != batch.platform.value:
        raise ValueError("消息发现批次平台与 Agent 运行不匹配")
    current = now or datetime.now(UTC)
    counts = {"discovered": len(batch.items), "imported": 0, "paused": 0, "skipped": 0}
    for item in batch.items:
        _record_state_sequence(
            session,
            run,
            item,
            [
                "OPENING_CONVERSATION",
                "VERIFYING_TARGET",
                "READING_MESSAGES",
                "BINDING_JOB",
            ],
        )
        if item.detail is None or item.reason_codes:
            _discovery_event(
                session,
                run,
                item,
                item.reason_codes or ["CONVERSATION_DETAIL_MISSING"],
            )
            counts["paused"] += 1
            continue
        job = _resolve_job(session, batch, item)
        score = _current_score(session, run, job) if job else None
        if job is None or score is None:
            _pause_existing_conversation(session, batch, item)
            _discovery_event(
                session,
                run,
                item,
                ["JOB_BINDING_NOT_UNIQUE"] if job is None else ["CURRENT_SCORE_MISSING"],
            )
            counts["paused"] += 1
            continue
        conversation_data = create_conversation(
            session,
            ConversationPayload(
                job_id=job.id,
                external_conversation_id=item.summary.external_conversation_id,
                recruiter_name=item.summary.recruiter_name,
                platform=batch.platform.value,
            ),
        )
        conversation = session.get(db.Conversation, conversation_data["id"])
        if conversation is None:
            raise RuntimeError("消息发现创建对话失败")
        if conversation.state in TERMINAL_CONVERSATION_STATES:
            counts["skipped"] += 1
            continue
        if not _acquire_conversation_lease(
            session, conversation.id, worker_id, current
        ):
            counts["skipped"] += 1
            continue
        detail = item.detail.conversation
        if detail is None:
            counts["paused"] += 1
            continue
        conversation.job_id = job.id
        conversation.strategy_id = run.strategy_id
        conversation.latest_job_score_id = score.id
        conversation.state = "ACTIVE"
        _record_state_sequence(session, run, item, ["DECIDING"])
        for message in detail.messages:
            if message.direction is not MessageDirection.INBOUND:
                continue
            before = session.scalar(
                select(db.Message.id).where(
                    db.Message.conversation_id == conversation.id,
                    db.Message.external_message_id == message.external_message_id,
                )
            )
            import_message(
                session,
                conversation.id,
                MessagePayload(
                    external_message_id=message.external_message_id,
                    content=message.content,
                    received_at=message.received_at,
                ),
            )
            if before is None:
                counts["imported"] += 1
        conversation.processing_lease_owner = None
        conversation.processing_lease_expires_at = None
        _discovery_event(session, run, item, ["CONVERSATION_IMPORTED"])
        _record_state_sequence(session, run, item, ["RETURNING_TO_LIST"])
    root_cursor = dict(run.cursor or {})
    root_cursor["message_discovery"] = {
        "discovery_state": "LIST_READY",
        "partition": batch.partition,
        "scroll_position": 0 if batch.exhausted else batch.scroll_position,
        "next_cursor": batch.next_cursor,
        "last_conversation_id": (
            batch.items[-1].summary.external_conversation_id if batch.items else None
        ),
        "last_message_id": (
            batch.items[-1].summary.last_message_id if batch.items else None
        ),
        "last_scan_at": batch.scanned_at.isoformat(),
        "seen_message_keys": batch.seen_message_keys,
        "exhausted": batch.exhausted,
    }
    run.cursor = root_cursor
    session.commit()
    return counts


def _resolve_job(
    session: Session,
    batch: MessageDiscoveryBatch,
    item: DiscoveredConversation,
) -> db.Job | None:
    if item.job_detail and item.job_detail.job:
        source = item.job_detail.job
        imported = import_job(
            session,
            JobImportPayload(
                external_job_id=source.external_job_id,
                title=source.title,
                company_name=source.company_name,
                industry=source.industry,
                location=source.location,
                work_mode=source.work_mode,
                salary_text=source.salary_text,
                description=source.description,
                source_status=source.source_status,
                source=batch.platform.value,
            ),
        )
        return session.get(db.Job, imported.job.id)
    detail = item.detail.conversation if item.detail else None
    external_job_id = item.summary.external_job_id or (
        detail.external_job_id if detail else None
    )
    if external_job_id:
        matches = session.scalars(
            select(db.Job).where(
                db.Job.user_id == DEFAULT_USER_ID,
                db.Job.source == batch.platform.value,
                db.Job.external_job_id == external_job_id,
            )
        ).all()
        return matches[0] if len(matches) == 1 else None
    title = item.summary.job_title or (detail.job_title if detail else None)
    company = item.summary.company_name or (detail.company_name if detail else None)
    if not title or not company:
        return None
    matches = session.scalars(
        select(db.Job).where(
            db.Job.user_id == DEFAULT_USER_ID,
            db.Job.source == batch.platform.value,
            db.Job.title == title,
            db.Job.company_name == company,
        )
    ).all()
    return matches[0] if len(matches) == 1 else None


def _current_score(
    session: Session, run: db.AgentRun, job: db.Job
) -> db.JobScore | None:
    strategy = session.get(db.JobStrategy, run.strategy_id)
    if strategy is None:
        return None
    profile = session.get(db.CandidateProfile, strategy.candidate_profile_id)
    if profile is None:
        return None
    return session.scalar(
        select(db.JobScore)
        .where(
            db.JobScore.job_id == job.id,
            db.JobScore.strategy_id == run.strategy_id,
            db.JobScore.strategy_version == strategy.version,
            db.JobScore.profile_version == profile.version,
            db.JobScore.effective_job_status == "OPEN",
        )
        .order_by(db.JobScore.created_at.desc())
        .limit(1)
    )


def _acquire_conversation_lease(
    session: Session,
    conversation_id: object,
    worker_id: str,
    now: datetime,
) -> bool:
    claimed = session.scalar(
        update(db.Conversation)
        .where(
            db.Conversation.id == conversation_id,
            or_(
                db.Conversation.processing_lease_expires_at.is_(None),
                db.Conversation.processing_lease_expires_at <= now,
                db.Conversation.processing_lease_owner == worker_id,
            ),
        )
        .values(
            processing_lease_owner=worker_id,
            processing_lease_expires_at=now + timedelta(seconds=30),
        )
        .returning(db.Conversation.id)
    )
    session.flush()
    return claimed is not None


def _pause_existing_conversation(
    session: Session,
    batch: MessageDiscoveryBatch,
    item: DiscoveredConversation,
) -> None:
    conversation = session.scalar(
        select(db.Conversation).where(
            db.Conversation.user_id == DEFAULT_USER_ID,
            db.Conversation.platform == batch.platform.value,
            db.Conversation.external_conversation_id
            == item.summary.external_conversation_id,
        )
    )
    if conversation:
        conversation.state = "PAUSED"


def _discovery_event(
    session: Session,
    run: db.AgentRun,
    item: DiscoveredConversation,
    reasons: list[str],
) -> None:
    session.add(
        db.AgentRunEvent(
            agent_run_id=run.id,
            event_type="CONVERSATION_DISCOVERY",
            entity_type="conversation",
            reason_codes=reasons,
            metadata_json={
                "external_conversation_id": item.summary.external_conversation_id,
                "recruiter_name": item.summary.recruiter_name,
                "job_title": item.summary.job_title,
            },
        )
    )


def _record_state_sequence(
    session: Session,
    run: db.AgentRun,
    item: DiscoveredConversation,
    states: list[str],
) -> None:
    for state in states:
        session.add(
            db.AgentRunEvent(
                agent_run_id=run.id,
                event_type="MESSAGE_DISCOVERY_STATE",
                entity_type="conversation",
                reason_codes=[],
                metadata_json={
                    "state": state,
                    "external_conversation_id":
                    item.summary.external_conversation_id,
                },
            )
        )
