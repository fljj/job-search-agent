import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.core.conversation_config import get_conversation_policy
from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import (
    ConversationPayload,
    DraftResponse,
    MessagePayload,
    MessageResponse,
)
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.job_service import get_job_entity, get_parsed_entity
from apps.api.app.services.knowledge_service import get_knowledge_entities
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.conversation_agent.generator import (
    GENERATOR_VERSION,
    generate_greeting,
    generate_reply,
)
from packages.conversation_agent.intents import classify_intents
from packages.conversation_agent.models import Decision, DraftResult
from packages.knowledge_base.models import KnowledgeFact

POLICY_VERSION = "conversation-policy-v1"


def create_conversation(session: Session, payload: ConversationPayload) -> dict[str, object]:
    ensure_default_user(session)
    get_job_entity(session, payload.job_id)
    existing = session.scalar(select(db.Conversation).where(
        db.Conversation.user_id == DEFAULT_USER_ID,
        db.Conversation.platform == payload.platform,
        db.Conversation.external_conversation_id == payload.external_conversation_id,
    ))
    if existing:
        return _conversation_response(existing)
    conversation = db.Conversation(user_id=DEFAULT_USER_ID, **payload.model_dump())
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _conversation_response(conversation)


def import_message(session: Session, conversation_id: object, payload: MessagePayload) -> MessageResponse:
    conversation = _get_conversation(session, conversation_id)
    existing = session.scalar(select(db.Message).where(
        db.Message.conversation_id == conversation.id,
        db.Message.external_message_id == payload.external_message_id,
    ))
    if existing:
        return _message_response(existing)
    intents = classify_intents(payload.content)
    message = db.Message(conversation_id=conversation.id, direction="INBOUND",
                         intents=[intent.value for intent in intents], **payload.model_dump())
    session.add(message)
    session.commit()
    session.refresh(message)
    return _message_response(message)


def create_reply_draft(session: Session, message_id: object) -> DraftResponse:
    message = session.get(db.Message, message_id)
    if message is None:
        raise ResourceNotFoundError("消息不存在")
    conversation = _get_conversation(session, message.conversation_id)
    fingerprint = _fingerprint("REPLY", message.id, message.content, _knowledge_versions(session))
    existing = session.scalar(select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint))
    if existing:
        return _draft_response(session, existing)
    result = generate_reply(message.content, _knowledge_facts(session), get_conversation_policy())
    return _persist_draft(session, result, fingerprint, "REPLY", conversation.id, message.id, None)


def create_greeting_draft(session: Session, job_score_id: object) -> DraftResponse:
    score = session.get(db.JobScore, job_score_id)
    if score is None:
        raise ResourceNotFoundError("评分不存在")
    job = get_job_entity(session, score.job_id)
    parsed = get_parsed_entity(session, score.parsed_job_detail_id)
    fingerprint = _fingerprint("GREETING", score.id, score.input_fingerprint, _knowledge_versions(session))
    existing = session.scalar(select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint))
    if existing:
        return _draft_response(session, existing)
    result = generate_greeting(job.title, job.company_name, job.industry,
                               parsed.required_skills + parsed.preferred_skills,
                               _knowledge_facts(session), get_conversation_policy())
    if score.hard_rejected or score.effective_job_status != "OPEN":
        result.decision = Decision.DENY
        result.reason_codes = ["JOB_NOT_ELIGIBLE_OR_OPEN"]
    return _persist_draft(session, result, fingerprint, "GREETING", None, None, score.id)


def list_confirmation_tasks(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(select(db.ConfirmationTask).where(
        db.ConfirmationTask.user_id == DEFAULT_USER_ID
    ).order_by(db.ConfirmationTask.created_at.desc())).all()
    result: list[dict[str, object]] = []
    for task in rows:
        decision = session.get(db.PolicyDecision, task.decision_id)
        if decision is None:
            continue
        draft = session.get(db.GeneratedDraft, decision.draft_id)
        result.append({"id": task.id, "status": task.status, "decision_id": decision.id,
                       "action_type": decision.action_type, "reason_codes": decision.reason_codes,
                       "draft_id": draft.id if draft else None,
                       "content": draft.content if draft else None,
                       "confidence": float(draft.confidence) if draft else None})
    return result


def _persist_draft(
    session: Session, result: DraftResult, fingerprint: str, draft_type: str,
    conversation_id: object | None, message_id: object | None, score_id: object | None,
) -> DraftResponse:
    draft = db.GeneratedDraft(
        user_id=DEFAULT_USER_ID, conversation_id=conversation_id, message_id=message_id,
        job_score_id=score_id, draft_type=draft_type, content=result.content,
        intents=[intent.value for intent in result.intents],
        fact_ids=[str(item) for item in result.fact_ids], confidence=Decimal(str(result.confidence)),
        risk_codes=result.risk_codes, input_fingerprint=fingerprint,
        generator_version=GENERATOR_VERSION,
    )
    session.add(draft)
    session.flush()
    decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID, draft_id=draft.id, action_type=draft_type,
        decision=result.decision.value if hasattr(result.decision, "value") else result.decision,
        reason_codes=result.reason_codes, policy_version=POLICY_VERSION,
        input_snapshot={"intents": [item.value for item in result.intents],
                        "fact_ids": [str(item) for item in result.fact_ids],
                        "confidence": result.confidence, "risk_codes": result.risk_codes},
    )
    session.add(decision)
    session.flush()
    if decision.decision != "DENY":
        policy = get_conversation_policy()
        session.add(db.ConfirmationTask(
            user_id=DEFAULT_USER_ID,
            decision_id=decision.id,
            expires_at=datetime.now(UTC) + timedelta(hours=policy.confirmation_ttl_hours),
        ))
    session.commit()
    session.refresh(draft)
    return _draft_response(session, draft)


def _draft_response(session: Session, draft: db.GeneratedDraft) -> DraftResponse:
    decision = session.scalar(select(db.PolicyDecision).where(
        db.PolicyDecision.draft_id == draft.id
    ).order_by(db.PolicyDecision.created_at.asc()).limit(1))
    if decision is None:
        raise RuntimeError("草稿缺少策略决策")
    task = session.scalar(select(db.ConfirmationTask).where(db.ConfirmationTask.decision_id == decision.id))
    return DraftResponse(id=draft.id, draft_type=draft.draft_type, content=draft.content,
                         intents=draft.intents, fact_ids=draft.fact_ids,
                         confidence=float(draft.confidence), risk_codes=draft.risk_codes,
                         decision=decision.decision, reason_codes=decision.reason_codes,
                         confirmation_task_id=task.id if task else None)


def _knowledge_facts(session: Session) -> list[KnowledgeFact]:
    return [KnowledgeFact(id=item.id, category=item.category, key=item.key, fact=item.fact,
                          source=item.source, allowed_for_auto_reply=item.allowed_for_auto_reply,
                          sensitivity=item.sensitivity, verified_at=item.verified_at,
                          valid_until=item.valid_until, version=item.version)
            for item in get_knowledge_entities(session)]


def _knowledge_versions(session: Session) -> list[tuple[str, int]]:
    return sorted((str(item.id), item.version) for item in get_knowledge_entities(session))


def _fingerprint(*parts: object) -> str:
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _get_conversation(session: Session, conversation_id: object) -> db.Conversation:
    conversation = session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    return conversation


def _conversation_response(conversation: db.Conversation) -> dict[str, object]:
    return {"id": conversation.id, "job_id": conversation.job_id,
            "strategy_id": conversation.strategy_id,
            "latest_job_score_id": conversation.latest_job_score_id,
            "platform": conversation.platform,
            "external_conversation_id": conversation.external_conversation_id,
            "recruiter_name": conversation.recruiter_name, "state": conversation.state}


def _message_response(message: db.Message) -> MessageResponse:
    return MessageResponse(id=message.id, conversation_id=message.conversation_id,
                           external_message_id=message.external_message_id,
                           content=message.content, intents=message.intents)
