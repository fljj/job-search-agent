import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.llm.errors import LlmProviderError
from apps.api.app.core.config import get_settings
from apps.api.app.core.conversation_config import get_conversation_policy
from apps.api.app.core.llm import build_llm_provider
from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import (
    ConversationPayload,
    DraftResponse,
    MessagePayload,
    MessageResponse,
)
from apps.api.app.schemas.score import ScoreRequest
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.job_service import get_job_entity, get_parsed_entity
from apps.api.app.services.knowledge_service import get_knowledge_entities
from apps.api.app.services.llm_service import record_llm_invocation
from apps.api.app.services.qualification_service import refresh_qualification
from apps.api.app.services.score_service import create_score
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.conversation_agent.intents import classify_intents, normalize_intents
from packages.conversation_agent.knowledge.profile_answer import CandidateKnowledge
from packages.conversation_agent.llm_engine import (
    build_llm_reply,
    build_mismatch_decline,
    has_valid_conversation_evidence,
)
from packages.conversation_agent.models import Decision, DraftResult, Intent, ReplySource
from packages.conversation_agent.router import ReplyRouteContext, route_reply
from packages.conversation_agent.rules.salary import SalaryExpectation
from packages.knowledge_base.models import KnowledgeFact
from packages.llm.models import (
    ConversationEvaluation,
    ConversationEvaluationRequest,
    ConversationMessage,
    GeneratedMessage,
    LlmCallMetadata,
    LlmResult,
    MessageClassification,
    MessageClassificationRequest,
    ReplyContext,
    TrustedFact,
)
from packages.llm.models import (
    GreetingRequest as LlmGreetingRequest,
)
from packages.llm.models import (
    ReplyRequest as LlmReplyRequest,
)
from packages.llm.ports import LlmProvider
from packages.policy_engine.content_check import validate_edited_content
from packages.resume_selector.selector import ResumeCandidate, select_default_resume

POLICY_VERSION = "conversation-policy-v1"
GENERATOR_VERSION = "conversation-llm-v4"


def create_conversation(session: Session, payload: ConversationPayload) -> dict[str, object]:
    ensure_default_user(session)
    if payload.job_id is not None:
        get_job_entity(session, payload.job_id)
    existing = session.scalar(
        select(db.Conversation).where(
            db.Conversation.user_id == DEFAULT_USER_ID,
            db.Conversation.platform == payload.platform,
            db.Conversation.external_conversation_id == payload.external_conversation_id,
        )
    )
    if existing:
        return _conversation_response(existing)
    conversation = db.Conversation(user_id=DEFAULT_USER_ID, **payload.model_dump())
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _conversation_response(conversation)


def reopen_conversation(
    session: Session,
    conversation_id: object,
) -> dict[str, object]:
    """按用户明确指示重新开启已结束会话，不创建或发送回复。"""
    conversation = _get_conversation(session, conversation_id)
    if conversation.state == "ACTIVE":
        return _conversation_response(conversation)
    before_state = conversation.state
    conversation.state = "ACTIVE"
    conversation.processing_lease_owner = None
    conversation.processing_lease_expires_at = None
    session.add(
        db.AuditEvent(
            user_id=DEFAULT_USER_ID,
            actor_type="USER",
            event_type="CONVERSATION_REOPENED",
            entity_type="conversation",
            entity_id=conversation.id,
            before_state=before_state,
            after_state="ACTIVE",
            reason_codes=["USER_CONFIRMED_REOPEN"],
            metadata_json={"platform": conversation.platform},
            correlation_id=f"conversation-reopen:{conversation.id}",
        )
    )
    session.commit()
    session.refresh(conversation)
    return _conversation_response(conversation)


IGNORED_PLATFORM_EVENTS = {
    "对方已查看了您的附件简历",
    "对方已查看您的附件简历",
}


def _is_ignored_platform_event(content: str) -> bool:
    return content.strip() in IGNORED_PLATFORM_EVENTS


def import_message(
    session: Session, conversation_id: object, payload: MessagePayload
) -> MessageResponse:
    conversation = _get_conversation(session, conversation_id)
    existing = session.scalar(
        select(db.Message).where(
            db.Message.conversation_id == conversation.id,
            db.Message.external_message_id == payload.external_message_id,
        )
    )
    if existing:
        return _message_response(existing)
    if _is_ignored_platform_event(payload.content):
        message = db.Message(
            conversation_id=conversation.id,
            direction="INBOUND",
            intents=[],
            status="PLATFORM_EVENT_IGNORED",
            **payload.model_dump(),
        )
        session.add(message)
        session.commit()
        session.refresh(message)
        return _message_response(message)
    previous = session.scalars(
        select(db.Message).where(
            db.Message.conversation_id == conversation.id,
            db.Message.direction == "INBOUND",
            db.Message.status == "RECEIVED",
            ~select(db.GeneratedDraft.id)
            .where(db.GeneratedDraft.message_id == db.Message.id)
            .exists(),
        )
    ).all()
    for item in previous:
        item.status = "SUPERSEDED"
    intents = classify_intents(payload.content)
    message = db.Message(
        conversation_id=conversation.id,
        direction="INBOUND",
        intents=[intent.value for intent in intents],
        **payload.model_dump(),
    )
    session.add(message)
    session.flush()
    refresh_qualification(session, conversation, message=message)
    session.commit()
    session.refresh(message)
    return _message_response(message)


def list_conversations(session: Session) -> list[dict[str, object]]:
    conversations = session.scalars(
        select(db.Conversation)
        .where(db.Conversation.user_id == DEFAULT_USER_ID)
        .order_by(db.Conversation.created_at.desc(), db.Conversation.id.desc())
    ).all()
    items: list[dict[str, object]] = []
    for conversation in conversations:
        job = session.get(db.Job, conversation.job_id) if conversation.job_id else None
        score = (
            session.get(db.JobScore, conversation.latest_job_score_id)
            if conversation.latest_job_score_id
            else None
        )
        draft = session.scalar(
            select(db.GeneratedDraft)
            .where(db.GeneratedDraft.conversation_id == conversation.id)
            .order_by(
                db.GeneratedDraft.created_at.desc(),
                db.GeneratedDraft.id.desc(),
            )
            .limit(1)
        )
        draft_decision = (
            session.scalar(
                select(db.PolicyDecision)
                .where(db.PolicyDecision.draft_id == draft.id)
                .order_by(
                    db.PolicyDecision.created_at.desc(),
                    db.PolicyDecision.id.desc(),
                )
                .limit(1)
            )
            if draft
            else None
        )
        resume_action = session.scalar(
            select(db.ActionQueue)
            .where(
                db.ActionQueue.conversation_id == conversation.id,
                db.ActionQueue.action_type == "RESUME",
            )
            .order_by(db.ActionQueue.created_at.desc(), db.ActionQueue.id.desc())
            .limit(1)
        )
        items.append(
            {
                "id": conversation.id,
                "platform": conversation.platform,
                "recruiter_name": conversation.recruiter_name,
                "state": conversation.state,
                "qualification_status": conversation.qualification_status,
                "qualification_evidence": conversation.qualification_evidence,
                "qualification_version": conversation.qualification_version,
                "company_name": (job.company_name if job else conversation.observed_company_name),
                "job_id": conversation.job_id,
                "job_title": (job.title if job else conversation.observed_job_title),
                "strategy_id": conversation.strategy_id,
                "latest_score": score.total_score if score else None,
                "latest_grade": score.grade if score else None,
                "latest_draft_type": draft.draft_type if draft else None,
                "latest_draft_content": draft.content if draft else None,
                "latest_reply_source": draft.reply_source if draft else None,
                "latest_draft_decision": (draft_decision.decision if draft_decision else None),
                "latest_draft_reason_codes": (
                    draft_decision.reason_codes if draft_decision else []
                ),
                "resume_action_status": resume_action.status if resume_action else None,
                "resume_attachment_name": (
                    resume_action.attachment_name if resume_action else None
                ),
            }
        )
    return items


def create_reply_draft(
    session: Session,
    message_id: object,
    provider: LlmProvider | None = None,
) -> DraftResponse:
    message = session.get(db.Message, message_id)
    if message is None:
        raise ResourceNotFoundError("消息不存在")
    if message.status == "SUPERSEDED":
        raise ValueError("消息已被同一会话中的后续消息聚合")
    conversation = _get_conversation(session, message.conversation_id)
    education_reply = _full_time_education_reply(session, message)
    if education_reply is not None:
        fingerprint = _fingerprint(
            "REPLY",
            message.id,
            "FULL_TIME_EDUCATION_DISCLOSURE",
            _knowledge_versions(session),
        )
        existing = session.scalar(
            select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
        )
        if existing:
            return _draft_response(session, existing)
        return _persist_draft(
            session,
            education_reply,
            fingerprint,
            "REPLY",
            conversation.id,
            message.id,
            None,
            reply_source=ReplySource.KNOWLEDGE_BASE,
        )
    qualification, qualification_evidence = refresh_qualification(
        session, conversation, message=message
    )
    if qualification.value == "MISMATCH":
        draft_type = "MISMATCH_DECLINE"
        fingerprint = _fingerprint(
            draft_type,
            conversation.id if draft_type == "MISMATCH_DECLINE" else message.id,
            conversation.qualification_version,
            _knowledge_versions(session),
        )
        existing = session.scalar(
            select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
        )
        if existing:
            return _draft_response(session, existing)
        prior_decline = session.scalar(
            select(db.GeneratedDraft).where(
                db.GeneratedDraft.conversation_id == conversation.id,
                db.GeneratedDraft.draft_type == "MISMATCH_DECLINE",
            )
        )
        if prior_decline:
            return _draft_response(session, prior_decline)
        result = build_mismatch_decline(qualification_evidence)
        message.status = "MISMATCH_DECLINED"
        return _persist_draft(
            session,
            result,
            fingerprint,
            draft_type,
            conversation.id,
            message.id,
            None,
            reply_source=ReplySource.RULE_TEMPLATE,
        )
    strategy = (
        session.get(db.JobStrategy, conversation.strategy_id) if conversation.strategy_id else None
    )
    route = route_reply(
        message.content,
        _reply_route_context(session, strategy),
    )
    if route.result is not None:
        fingerprint = _fingerprint(
            "REPLY",
            message.id,
            route.source,
            route.result.reason_codes,
            strategy.version if strategy else None,
            _knowledge_versions(session),
        )
        existing = session.scalar(
            select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
        )
        if existing:
            return _draft_response(session, existing)
        return _persist_draft(
            session,
            route.result,
            fingerprint,
            "REPLY",
            conversation.id,
            message.id,
            None,
            reply_source=route.source,
        )
    if qualification.value == "UNKNOWN":
        fingerprint = _fingerprint(
            "REPLY",
            message.id,
            "SAFE_JOB_CLARIFICATION",
            conversation.qualification_version,
        )
        existing = session.scalar(
            select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
        )
        if existing:
            return _draft_response(session, existing)
        return _persist_draft(
            session,
            DraftResult(
                content="感谢联系。方便介绍一下岗位方向、工作地点、工作模式和大致薪资范围吗？",
                intents=[Intent.JOB_DETAIL],
                confidence=1,
                risk_codes=["JOB_CONTEXT_INCOMPLETE"],
                decision=Decision.ALLOW_AUTO,
                reason_codes=["SAFE_JOB_CLARIFICATION"],
            ),
            fingerprint,
            "REPLY",
            conversation.id,
            message.id,
            None,
            reply_source=ReplySource.RULE_TEMPLATE,
        )
    llm_provider = _optional_llm_provider(provider)
    score = _current_score(session, conversation)
    if (
        score is None
        and qualification.value == "FULL_MATCH"
        and conversation.job_id
        and llm_provider is not None
    ):
        try:
            score = _bind_current_score(session, conversation, llm_provider)
        except LlmProviderError:
            score = None
    draft_type = "REPLY"
    fingerprint = _fingerprint(
        draft_type,
        message.id,
        score.input_fingerprint if score else None,
        conversation.qualification_version,
        _knowledge_versions(session),
    )
    existing = session.scalar(
        select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
    )
    if existing:
        return _draft_response(session, existing)
    reply_source = ReplySource.LLM
    if score is not None and llm_provider is not None:
        try:
            result = _build_scored_reply(
                session,
                conversation,
                message,
                score,
                llm_provider,
            )
        except LlmProviderError as exc:
            result = _llm_failure_handoff(exc.code)
            reply_source = ReplySource.HUMAN
    else:
        result = _llm_failure_handoff("LLM_UNAVAILABLE")
        reply_source = ReplySource.HUMAN
    return _persist_draft(
        session,
        result,
        fingerprint,
        draft_type,
        conversation.id,
        message.id,
        score.id if score else None,
        reply_source=reply_source,
    )


def create_greeting_draft(
    session: Session,
    job_score_id: object,
    provider: LlmProvider | None = None,
) -> DraftResponse:
    score = session.get(db.JobScore, job_score_id)
    if score is None:
        raise ResourceNotFoundError("评分不存在")
    job = get_job_entity(session, score.job_id)
    parsed = get_parsed_entity(session, score.parsed_job_detail_id)
    profile = session.get(db.CandidateProfile, score.candidate_profile_id)
    if profile is None:
        raise ResourceNotFoundError("候选人资料不存在")
    fingerprint = _fingerprint(
        "GREETING",
        GENERATOR_VERSION,
        score.id,
        score.input_fingerprint,
        _knowledge_versions(session),
        profile.version,
    )
    existing = session.scalar(
        select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
    )
    if existing:
        return _draft_response(session, existing)
    llm_provider = provider or build_llm_provider(get_settings())
    profile_facts = _profile_facts(
        profile,
        parsed.required_skills + parsed.preferred_skills,
    )
    facts = profile_facts + [
        fact for fact in _knowledge_facts(session) if fact.category.upper() != "EDUCATION"
    ]
    usable = [
        fact
        for fact in facts
        if fact.id
        and fact.allowed_for_auto_reply
        and fact.sensitivity.value == "NORMAL"
        and fact.is_current(datetime.now(UTC))
    ][:5]
    generated = _call_llm(
        session,
        llm_provider,
        "GREETING",
        "generate_greeting",
        fingerprint,
        lambda: llm_provider.generate_greeting(
            LlmGreetingRequest(
                company_name=job.company_name,
                job_title=job.title,
                matched_skills=[fact.fact for fact in profile_facts[1:5]],
                facts=[TrustedFact(id=fact.id, content=fact.fact) for fact in usable if fact.id],
            )
        ),
    ).data
    result = build_llm_reply(
        MessageClassification(intents=[Intent.JOB_DETAIL], confidence=Decimal("1")),
        generated,
        facts,
        get_conversation_policy(),
        now=datetime.now(UTC),
    )
    if score.hard_rejected or score.effective_job_status != "OPEN" or not score.automation_eligible:
        result.decision = Decision.DENY
        result.reason_codes = ["JOB_NOT_ELIGIBLE_OR_OPEN"]
    return _persist_draft(
        session,
        result,
        fingerprint,
        "GREETING",
        None,
        None,
        score.id,
        reply_source=ReplySource.LLM,
    )


def create_resume_draft(
    session: Session,
    message_id: object,
    provider: LlmProvider | None = None,
) -> DraftResponse:
    message = session.get(db.Message, message_id)
    if message is None:
        raise ResourceNotFoundError("消息不存在")
    conversation = _get_conversation(session, message.conversation_id)
    qualification, qualification_evidence = refresh_qualification(
        session, conversation, message=message
    )
    score = (
        session.get(db.JobScore, conversation.latest_job_score_id)
        if conversation.latest_job_score_id
        else None
    )
    messages = session.scalars(
        select(db.Message)
        .where(
            db.Message.conversation_id == conversation.id,
            db.Message.direction == "INBOUND",
        )
        .order_by(db.Message.received_at.asc())
    ).all()
    explicit_request = "RESUME_REQUEST" in message.intents
    if explicit_request:
        evaluation = ConversationEvaluation(
            resume_requested=True,
            positive_feedback=False,
            evidence_message_ids=[message.id],
            confidence=Decimal("1"),
        )
        authorization_basis = "INBOUND_EXPLICIT_RESUME_REQUEST"
    else:
        llm_provider = _optional_llm_provider(provider)
        if llm_provider is None:
            evaluation = ConversationEvaluation(
                resume_requested=False,
                positive_feedback=False,
                evidence_message_ids=[],
                confidence=Decimal("0"),
            )
        else:
            evaluation = _call_llm(
                session,
                llm_provider,
                "CONVERSATION_EVALUATE",
                "evaluate_conversation",
                _fingerprint(
                    conversation.id,
                    [(item.id, item.content) for item in messages],
                ),
                lambda: llm_provider.evaluate_conversation(
                    ConversationEvaluationRequest(
                        messages=[
                            ConversationMessage(id=item.id, content=item.content)
                            for item in messages
                        ]
                    )
                ),
            ).data
        authorization_basis = "INBOUND_POSITIVE_FEEDBACK"
    valid_message_ids = {item.id for item in messages}
    evidence_valid = has_valid_conversation_evidence(evaluation, valid_message_ids)
    resumes = session.scalars(
        select(db.Resume)
        .where(
            db.Resume.user_id == DEFAULT_USER_ID,
            db.Resume.platform == conversation.platform,
            db.Resume.is_available.is_(True),
        )
        .order_by(db.Resume.created_at.asc(), db.Resume.id.asc())
    ).all()
    selected = select_default_resume(
        [
            ResumeCandidate(
                id=item.id,
                attachment_name=item.attachment_name,
                target_directions=item.target_directions,
                is_available=item.is_available,
            )
            for item in resumes
        ]
    )
    duplicate = bool(
        selected
        and session.scalar(
            select(db.ResumeSendRecord).where(
                db.ResumeSendRecord.conversation_id == conversation.id,
                db.ResumeSendRecord.resume_id == selected.id,
            )
        )
    )
    allowed = (
        qualification.value != "MISMATCH"
        and evidence_valid
        and (evaluation.resume_requested or evaluation.positive_feedback)
        and selected is not None
        and not duplicate
    )
    fingerprint = _fingerprint(
        "RESUME",
        "DEFAULT_PLATFORM_RESUME_V1",
        conversation.id,
        selected.id if selected else None,
        evaluation.evidence_message_ids,
        conversation.qualification_version,
        qualification.value,
        qualification_evidence,
    )
    existing = session.scalar(
        select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
    )
    if existing:
        return _draft_response(session, existing)
    result = DraftResult(
        content=selected.attachment_name if selected else "无可用的默认简历附件",
        intents=[Intent.RESUME_REQUEST],
        confidence=float(evaluation.confidence),
        risk_codes=[] if allowed else ["RESUME_SEND_CONDITIONS_NOT_MET"],
        decision=Decision.ALLOW_AUTO if allowed else Decision.DENY,
        reason_codes=(
            ["INBOUND_RESUME_REQUEST_ALLOWED"]
            if allowed
            else ["RESUME_SEND_DENIED", *qualification_evidence]
        ),
    )
    return _persist_draft(
        session,
        result,
        fingerprint,
        "RESUME",
        conversation.id,
        message.id,
        score.id if score else None,
        resume_id=selected.id if selected else None,
        decision_metadata={
            "authorization_basis": authorization_basis,
            "evidence_message_ids": [str(item) for item in evaluation.evidence_message_ids],
            "qualification": {
                "status": qualification.value,
                "evidence": qualification_evidence,
                "version": conversation.qualification_version,
            },
        },
        reply_source=ReplySource.RULE_TEMPLATE,
    )


def edit_draft(session: Session, draft_id: object, content: str) -> DraftResponse:
    original = session.get(db.GeneratedDraft, draft_id)
    if original is None or original.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("草稿不存在")
    if session.scalar(select(db.ActionQueue.id).where(db.ActionQueue.draft_id == original.id)):
        raise ValueError("草稿已经创建动作，不能直接修改")
    risks = validate_edited_content(content)
    if risks:
        raise ValueError("修改后的内容未通过敏感信息检查")
    original_decision = session.scalar(
        select(db.PolicyDecision)
        .where(db.PolicyDecision.draft_id == original.id)
        .order_by(db.PolicyDecision.created_at.asc())
        .limit(1)
    )
    if original_decision is None:
        raise RuntimeError("草稿缺少策略决策")
    fingerprint = _fingerprint("USER_EDIT", original.id, content)
    existing = session.scalar(
        select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
    )
    if existing:
        return _draft_response(session, existing)
    edited = db.GeneratedDraft(
        user_id=DEFAULT_USER_ID,
        conversation_id=original.conversation_id,
        message_id=original.message_id,
        job_score_id=original.job_score_id,
        draft_type=original.draft_type,
        content=content,
        intents=original.intents,
        fact_ids=original.fact_ids,
        confidence=original.confidence,
        risk_codes=["USER_EDITED_CONTENT"],
        input_fingerprint=fingerprint,
        generator_version="manual-user-edit-v1",
        reply_source=ReplySource.HUMAN.value,
    )
    session.add(edited)
    session.flush()
    decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=edited.id,
        action_type=original_decision.action_type,
        decision=original_decision.decision,
        reason_codes=["USER_EDIT_REVALIDATED"],
        policy_version=POLICY_VERSION,
        input_snapshot={
            **original_decision.input_snapshot,
            "supersedes_draft_id": str(original.id),
        },
    )
    session.add(decision)
    session.flush()
    if decision.decision == Decision.REQUIRE_CONFIRMATION.value:
        policy = get_conversation_policy()
        session.add(
            db.ConfirmationTask(
                user_id=DEFAULT_USER_ID,
                decision_id=decision.id,
                expires_at=datetime.now(UTC) + timedelta(hours=policy.confirmation_ttl_hours),
            )
        )
    prior_tasks = session.scalars(
        select(db.ConfirmationTask)
        .join(db.PolicyDecision, db.PolicyDecision.id == db.ConfirmationTask.decision_id)
        .where(
            db.PolicyDecision.draft_id == original.id,
            db.ConfirmationTask.status == "PENDING_APPROVAL",
        )
    ).all()
    for task in prior_tasks:
        task.status = "SUPERSEDED"
    session.add(
        db.AuditEvent(
            user_id=DEFAULT_USER_ID,
            actor_type="USER",
            event_type="DRAFT_EDITED",
            entity_type="draft",
            entity_id=edited.id,
            before_state="DRAFT",
            after_state="DRAFT",
            reason_codes=["USER_EDIT_REVALIDATED"],
            metadata_json={"supersedes_draft_id": str(original.id)},
            correlation_id=str(edited.id),
        )
    )
    session.commit()
    session.refresh(edited)
    return _draft_response(session, edited)


def list_confirmation_tasks(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(
        select(db.ConfirmationTask)
        .where(db.ConfirmationTask.user_id == DEFAULT_USER_ID)
        .order_by(db.ConfirmationTask.created_at.desc())
    ).all()
    result: list[dict[str, object]] = []
    for task in rows:
        decision = session.get(db.PolicyDecision, task.decision_id)
        if decision is None:
            continue
        draft = session.get(db.GeneratedDraft, decision.draft_id)
        result.append(
            {
                "id": task.id,
                "status": task.status,
                "decision_id": decision.id,
                "action_type": decision.action_type,
                "reason_codes": decision.reason_codes,
                "draft_id": draft.id if draft else None,
                "content": draft.content if draft else None,
                "confidence": float(draft.confidence) if draft else None,
            }
        )
    return result


def _persist_draft(
    session: Session,
    result: DraftResult,
    fingerprint: str,
    draft_type: str,
    conversation_id: object | None,
    message_id: object | None,
    score_id: object | None,
    resume_id: object | None = None,
    decision_metadata: dict[str, object] | None = None,
    reply_source: ReplySource = ReplySource.LLM,
) -> DraftResponse:
    draft = db.GeneratedDraft(
        user_id=DEFAULT_USER_ID,
        conversation_id=conversation_id,
        message_id=message_id,
        job_score_id=score_id,
        draft_type=draft_type,
        content=result.content,
        intents=[intent.value for intent in result.intents],
        fact_ids=[str(item) for item in result.fact_ids],
        confidence=Decimal(str(result.confidence)),
        risk_codes=result.risk_codes,
        input_fingerprint=fingerprint,
        generator_version=GENERATOR_VERSION,
        reply_source=reply_source.value,
    )
    session.add(draft)
    session.flush()
    decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=draft.id,
        action_type=draft_type,
        decision=result.decision.value if hasattr(result.decision, "value") else result.decision,
        reason_codes=result.reason_codes,
        policy_version=POLICY_VERSION,
        input_snapshot={
            "intents": [item.value for item in result.intents],
            "fact_ids": [str(item) for item in result.fact_ids],
            "confidence": result.confidence,
            "risk_codes": result.risk_codes,
            "resume_id": str(resume_id) if resume_id else None,
            **(decision_metadata or {}),
        },
    )
    session.add(decision)
    session.flush()
    if decision.decision == "REQUIRE_CONFIRMATION":
        policy = get_conversation_policy()
        session.add(
            db.ConfirmationTask(
                user_id=DEFAULT_USER_ID,
                decision_id=decision.id,
                expires_at=datetime.now(UTC) + timedelta(hours=policy.confirmation_ttl_hours),
            )
        )
    session.commit()
    session.refresh(draft)
    return _draft_response(session, draft)


def _draft_response(session: Session, draft: db.GeneratedDraft) -> DraftResponse:
    decision = session.scalar(
        select(db.PolicyDecision)
        .where(db.PolicyDecision.draft_id == draft.id)
        .order_by(db.PolicyDecision.created_at.asc())
        .limit(1)
    )
    if decision is None:
        raise RuntimeError("草稿缺少策略决策")
    task = session.scalar(
        select(db.ConfirmationTask).where(db.ConfirmationTask.decision_id == decision.id)
    )
    return DraftResponse(
        id=draft.id,
        draft_type=draft.draft_type,
        content=draft.content,
        reply_source=draft.reply_source,
        intents=draft.intents,
        fact_ids=draft.fact_ids,
        confidence=float(draft.confidence),
        risk_codes=draft.risk_codes,
        decision=decision.decision,
        reason_codes=decision.reason_codes,
        confirmation_task_id=task.id if task else None,
        resume_id=(
            decision.input_snapshot.get("resume_id")
            if decision.input_snapshot.get("resume_id")
            else None
        ),
    )


def _knowledge_facts(session: Session) -> list[KnowledgeFact]:
    return [
        KnowledgeFact(
            id=item.id,
            category=item.category,
            key=item.key,
            fact=item.fact,
            source=item.source,
            allowed_for_auto_reply=item.allowed_for_auto_reply,
            sensitivity=item.sensitivity,
            verified_at=item.verified_at,
            valid_until=item.valid_until,
            version=item.version,
        )
        for item in get_knowledge_entities(session)
    ]


def _knowledge_versions(session: Session) -> list[tuple[str, int]]:
    return sorted((str(item.id), item.version) for item in get_knowledge_entities(session))


def _reply_route_context(
    session: Session,
    strategy: db.JobStrategy | None,
) -> ReplyRouteContext:
    profile = session.scalar(
        select(db.CandidateProfile).where(db.CandidateProfile.user_id == DEFAULT_USER_ID)
    )
    facts = [
        fact
        for fact in _knowledge_facts(session)
        if fact.allowed_for_auto_reply
        and fact.sensitivity.value == "NORMAL"
        and fact.is_current(datetime.now(UTC))
    ]
    candidate_knowledge = (
        CandidateKnowledge(
            name=profile.name,
            total_years=profile.total_years,
            management_years=profile.management_years,
            profile_id=profile.id,
            skills=[
                (skill.id, skill.name, skill.years)
                for skill in sorted(
                    profile.skills,
                    key=lambda item: (not item.is_core, item.normalized_name),
                )
            ],
            facts=facts,
        )
        if profile is not None
        else None
    )
    if strategy is None:
        return ReplyRouteContext(candidate_knowledge=candidate_knowledge)
    return ReplyRouteContext(
        arrival_time_reply=strategy.arrival_time_reply,
        salary_expectations=[
            SalaryExpectation(
                work_mode=rule.work_mode,
                currency=rule.currency,
                expected_monthly_k=rule.expected_monthly_k,
            )
            for rule in sorted(
                strategy.salary_rules,
                key=lambda item: item.work_mode,
            )
        ],
        onsite_locations=[
            location.location_name
            for rule in strategy.work_mode_rules
            if rule.enabled and rule.work_mode == "ONSITE"
            for location in rule.locations
        ],
        enabled_work_modes=[rule.work_mode for rule in strategy.work_mode_rules if rule.enabled],
        candidate_knowledge=candidate_knowledge,
    )


def _reply_context(
    job: db.Job,
    parsed: db.ParsedJobDetail,
    score: db.JobScore,
    strategy: db.JobStrategy,
) -> ReplyContext:
    enabled_modes = [item.work_mode for item in strategy.work_mode_rules if item.enabled]
    onsite_locations = [
        location.location_name
        for item in strategy.work_mode_rules
        if item.enabled and item.work_mode == "ONSITE"
        for location in item.locations
    ]
    return ReplyContext(
        company_name=job.company_name,
        job_title=job.title,
        job_location=job.location,
        work_mode=job.work_mode,
        required_skills=parsed.required_skills,
        preferred_skills=parsed.preferred_skills,
        total_score=score.total_score,
        dimension_scores={
            "title": score.title_score,
            "skills": score.skill_score,
            "experience": score.experience_score,
            "location": score.location_score,
            "salary": score.salary_score,
            "industry": score.industry_score,
            "management": score.management_score,
        },
        match_reasons=score.match_reasons,
        risk_notes=score.risk_notes,
        enabled_work_modes=enabled_modes,
        allowed_onsite_locations=onsite_locations,
        remote_preferred=any(
            item.enabled
            and item.work_mode == "REMOTE"
            and all(
                not other.enabled
                or other.work_mode == "REMOTE"
                or item.location_score >= other.location_score
                for other in strategy.work_mode_rules
            )
            for item in strategy.work_mode_rules
        ),
    )


def _profile_facts(
    profile: db.CandidateProfile,
    desired_skills: list[str],
) -> list[KnowledgeFact]:
    """将用户已确认的候选人资料作为可追溯事实提供给招呼语生成器。"""
    verified_at = profile.updated_at or profile.created_at or datetime.now(UTC)
    experience_parts = [f"拥有{profile.total_years:g}年工作经验"]
    if profile.management_years:
        experience_parts.append(f"其中{profile.management_years:g}年管理经验")
    if profile.has_architecture_experience:
        experience_parts.append("具备架构经验")
    if profile.has_core_system_experience:
        experience_parts.append("具备核心系统经验")
    facts = [
        KnowledgeFact(
            id=profile.id,
            category="PROFILE",
            key="experience_summary",
            fact="，".join(experience_parts),
            source="candidate_profile",
            allowed_for_auto_reply=True,
            sensitivity="NORMAL",
            verified_at=verified_at,
            version=profile.version,
        )
    ]
    normalized_targets = [item.casefold().replace(" ", "") for item in desired_skills]
    skills = sorted(
        profile.skills,
        key=lambda skill: (
            0
            if any(
                skill.normalized_name.casefold().replace(" ", "") in target
                or target in skill.normalized_name.casefold().replace(" ", "")
                for target in normalized_targets
            )
            else 1,
            0 if skill.is_core else 1,
            skill.normalized_name,
        ),
    )
    facts.extend(
        KnowledgeFact(
            id=skill.id,
            category="SKILL",
            key=skill.normalized_name,
            fact=(
                f"具备{skill.name}技能"
                + (f"，相关经验{skill.years:g}年" if skill.years is not None else "")
            ),
            source=skill.source,
            allowed_for_auto_reply=True,
            sensitivity="NORMAL",
            verified_at=skill.updated_at or skill.created_at or verified_at,
            version=profile.version,
        )
        for skill in skills
    )
    return facts


def _safe_job_detail_clarification() -> DraftResult:
    return DraftResult(
        content=(
            "感谢联系，这个方向与我目前考虑的大致一致。方便补充一下岗位职责、技术重点和薪资范围吗？"
        ),
        intents=[Intent.JOB_DETAIL],
        confidence=1,
        risk_codes=["LLM_OR_FORMAL_SCORE_UNAVAILABLE"],
        decision=Decision.ALLOW_AUTO,
        reason_codes=["SAFE_JOB_DETAIL_CLARIFICATION"],
    )


def _llm_failure_handoff(failure_code: str) -> DraftResult:
    return DraftResult(
        content="这条消息需要人工确认后回复。",
        intents=[Intent.UNCLEAR],
        confidence=0,
        risk_codes=[failure_code],
        decision=Decision.REQUIRE_CONFIRMATION,
        reason_codes=["LLM_FAILURE_REQUIRES_HUMAN"],
    )


def _build_scored_reply(
    session: Session,
    conversation: db.Conversation,
    message: db.Message,
    score: db.JobScore,
    provider: LlmProvider,
) -> DraftResult:
    job = get_job_entity(session, conversation.job_id)
    parsed = get_parsed_entity(session, score.parsed_job_detail_id)
    strategy = session.get(db.JobStrategy, score.strategy_id)
    profile = session.get(db.CandidateProfile, score.candidate_profile_id)
    if strategy is None or profile is None:
        raise ValueError("回复上下文缺少策略或候选人资料")
    facts = _profile_facts(profile, parsed.required_skills + parsed.preferred_skills) + [
        fact for fact in _knowledge_facts(session) if fact.category.upper() != "EDUCATION"
    ]
    recent = session.scalars(
        select(db.Message)
        .where(db.Message.conversation_id == conversation.id)
        .order_by(db.Message.received_at.desc())
        .limit(20)
    ).all()
    classification = _call_llm(
        session,
        provider,
        "MESSAGE_CLASSIFY",
        "classify_message",
        _fingerprint(message.id, message.content),
        lambda: provider.classify_message(
            MessageClassificationRequest(
                message=message.content,
                recent_messages=[item.content for item in reversed(recent)],
            )
        ),
    ).data
    classification.intents = normalize_intents(message.content, classification.intents)
    message.intents = [intent.value for intent in classification.intents]
    usable = [
        fact
        for fact in facts
        if fact.id
        and fact.allowed_for_auto_reply
        and fact.sensitivity.value == "NORMAL"
        and fact.is_current(datetime.now(UTC))
    ][:20]
    generated: GeneratedMessage | None = None
    if usable and not {"SENSITIVE", "INTERVIEW_TIME"}.intersection(message.intents):
        generated = _call_llm(
            session,
            provider,
            "REPLY",
            "generate_reply",
            _fingerprint(message.id, [str(fact.id) for fact in usable]),
            lambda: provider.generate_reply(
                LlmReplyRequest(
                    incoming_message=message.content,
                    recent_messages=[item.content for item in reversed(recent)],
                    facts=[
                        TrustedFact(id=fact.id, content=fact.fact) for fact in usable if fact.id
                    ],
                    context=_reply_context(job, parsed, score, strategy),
                )
            ),
        ).data
    return build_llm_reply(
        classification,
        generated,
        facts,
        get_conversation_policy(),
        now=datetime.now(UTC),
    )


def _full_time_education_reply(
    session: Session,
    message: db.Message,
) -> DraftResult | None:
    inquiry_terms = ("全日制", "统招", "学历性质")
    if not any(term in message.content for term in inquiry_terms):
        return None
    profile = session.scalar(
        select(db.CandidateProfile).where(db.CandidateProfile.user_id == DEFAULT_USER_ID)
    )
    if profile is None or profile.bachelor_full_time is not False:
        return None
    fact = session.scalar(
        select(db.KnowledgeItem).where(
            db.KnowledgeItem.user_id == DEFAULT_USER_ID,
            db.KnowledgeItem.category == "EDUCATION",
            db.KnowledgeItem.normalized_key == "本科学习形式",
            db.KnowledgeItem.allowed_for_auto_reply.is_(True),
            db.KnowledgeItem.sensitivity == "NORMAL",
        )
    )
    return DraftResult(
        content="我的本科不是全日制，供您确认是否符合岗位要求。",
        intents=[Intent.EDUCATION],
        fact_ids=[fact.id] if fact else [],
        confidence=1,
        risk_codes=[],
        decision=Decision.ALLOW_AUTO,
        reason_codes=["FULL_TIME_EDUCATION_QUESTION_ANSWERED"],
    )


def _optional_llm_provider(provider: LlmProvider | None) -> LlmProvider | None:
    if provider is not None:
        return provider
    try:
        return build_llm_provider(get_settings())
    except LlmProviderError:
        return None


def _current_score(
    session: Session,
    conversation: db.Conversation,
) -> db.JobScore | None:
    if conversation.latest_job_score_id is None:
        return None
    score = session.get(db.JobScore, conversation.latest_job_score_id)
    if score is None:
        return None
    strategy = session.get(db.JobStrategy, score.strategy_id)
    profile = session.get(db.CandidateProfile, score.candidate_profile_id)
    if (
        strategy is None
        or profile is None
        or score.strategy_version != strategy.version
        or score.profile_version != profile.version
    ):
        return None
    return score


def _bind_current_score(
    session: Session,
    conversation: db.Conversation,
    provider: LlmProvider,
) -> db.JobScore:
    strategies = session.scalars(
        select(db.JobStrategy).where(
            db.JobStrategy.user_id == DEFAULT_USER_ID,
            db.JobStrategy.enabled.is_(True),
        )
    ).all()
    if conversation.strategy_id:
        strategies = [item for item in strategies if item.id == conversation.strategy_id]
    candidates: list[tuple[db.JobScore, int]] = []
    for strategy in strategies:
        profile = session.get(db.CandidateProfile, strategy.candidate_profile_id)
        if profile is None:
            continue
        score = session.scalar(
            select(db.JobScore)
            .where(
                db.JobScore.job_id == conversation.job_id,
                db.JobScore.strategy_id == strategy.id,
                db.JobScore.scoring_version.like("llm:%"),
            )
            .order_by(db.JobScore.created_at.desc())
            .limit(1)
        )
        if (
            score is None
            or score.strategy_version != strategy.version
            or score.profile_version != profile.version
        ):
            response = create_score(
                session,
                conversation.job_id,
                ScoreRequest(
                    strategy_id=strategy.id,
                    candidate_profile_id=profile.id,
                ),
                provider=provider,
            )
            score = session.get(db.JobScore, response.id)
        if score is not None:
            candidates.append((score, strategy.priority))
    if not candidates:
        raise ValueError("当前对话没有可用的启用策略评分")
    eligible = [item for item in candidates if not item[0].hard_rejected]
    selected = (
        min(eligible, key=lambda item: (-item[0].total_score, item[1]))
        if eligible
        else min(candidates, key=lambda item: item[1])
    )[0]
    conversation.strategy_id = selected.strategy_id
    conversation.latest_job_score_id = selected.id
    session.flush()
    return selected


def _call_llm[LlmDataT](
    session: Session,
    provider: LlmProvider,
    purpose: str,
    method: str,
    input_hash: str,
    call: Callable[[], LlmResult[LlmDataT]],
) -> LlmResult[LlmDataT]:
    try:
        result = call()
    except LlmProviderError as exc:
        record_llm_invocation(
            session,
            user_id=DEFAULT_USER_ID,
            purpose=purpose,
            input_hash=input_hash,
            status="FAILED",
            metadata=LlmCallMetadata(
                provider=provider.provider_name,
                model=provider.model_name,
                prompt_version=provider.prompt_version(method),
                latency_ms=0,
                attempt_number=exc.attempt_number,
            ),
            failure_code=exc.code,
        )
        session.commit()
        raise
    record_llm_invocation(
        session,
        user_id=DEFAULT_USER_ID,
        purpose=purpose,
        input_hash=input_hash,
        status="SUCCEEDED",
        metadata=result.metadata,
    )
    return result


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
    return {
        "id": conversation.id,
        "job_id": conversation.job_id,
        "strategy_id": conversation.strategy_id,
        "latest_job_score_id": conversation.latest_job_score_id,
        "observed_company_name": conversation.observed_company_name,
        "observed_job_title": conversation.observed_job_title,
        "observed_external_job_id": conversation.observed_external_job_id,
        "qualification_status": conversation.qualification_status,
        "qualification_evidence": conversation.qualification_evidence,
        "qualification_version": conversation.qualification_version,
        "platform": conversation.platform,
        "external_conversation_id": conversation.external_conversation_id,
        "recruiter_name": conversation.recruiter_name,
        "state": conversation.state,
    }


def _message_response(message: db.Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        external_message_id=message.external_message_id,
        content=message.content,
        intents=message.intents,
    )
