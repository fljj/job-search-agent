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
from apps.api.app.services.score_service import create_score
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.conversation_agent.intents import classify_intents
from packages.conversation_agent.llm_engine import (
    build_llm_reply,
    build_low_score_decline,
    has_valid_conversation_evidence,
)
from packages.conversation_agent.models import Decision, DraftResult, Intent
from packages.knowledge_base.models import KnowledgeFact
from packages.llm.models import (
    ConversationEvaluationRequest,
    ConversationMessage,
    GeneratedMessage,
    LlmCallMetadata,
    LlmResult,
    MessageClassification,
    MessageClassificationRequest,
    TrustedFact,
)
from packages.llm.models import (
    GreetingRequest as LlmGreetingRequest,
)
from packages.llm.models import (
    ReplyRequest as LlmReplyRequest,
)
from packages.llm.ports import LlmProvider
from packages.resume_selector.selector import ResumeCandidate, select_resume

POLICY_VERSION = "conversation-policy-v1"
GENERATOR_VERSION = "conversation-llm-v1"


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


def list_conversations(session: Session) -> list[dict[str, object]]:
    conversations = session.scalars(
        select(db.Conversation)
        .where(db.Conversation.user_id == DEFAULT_USER_ID)
        .order_by(db.Conversation.created_at.desc())
    ).all()
    items: list[dict[str, object]] = []
    for conversation in conversations:
        job = session.get(db.Job, conversation.job_id)
        score = (
            session.get(db.JobScore, conversation.latest_job_score_id)
            if conversation.latest_job_score_id
            else None
        )
        draft = session.scalar(
            select(db.GeneratedDraft)
            .where(db.GeneratedDraft.conversation_id == conversation.id)
            .order_by(db.GeneratedDraft.created_at.desc())
            .limit(1)
        )
        resume_action = session.scalar(
            select(db.ActionQueue)
            .where(
                db.ActionQueue.conversation_id == conversation.id,
                db.ActionQueue.action_type == "RESUME",
            )
            .order_by(db.ActionQueue.created_at.desc())
            .limit(1)
        )
        items.append(
            {
                "id": conversation.id,
                "platform": conversation.platform,
                "recruiter_name": conversation.recruiter_name,
                "state": conversation.state,
                "company_name": job.company_name if job else None,
                "job_title": job.title if job else None,
                "strategy_id": conversation.strategy_id,
                "latest_score": score.total_score if score else None,
                "latest_grade": score.grade if score else None,
                "latest_draft_type": draft.draft_type if draft else None,
                "latest_draft_content": draft.content if draft else None,
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
    conversation = _get_conversation(session, message.conversation_id)
    llm_provider = provider or build_llm_provider(get_settings())
    score = _bind_current_score(session, conversation, llm_provider)
    draft_type = "LOW_SCORE_DECLINE" if score.total_score < 60 else "REPLY"
    blocked = score.hard_rejected or score.effective_job_status != "OPEN"
    fingerprint = _fingerprint(
        draft_type,
        conversation.id if draft_type == "LOW_SCORE_DECLINE" else message.id,
        score.input_fingerprint,
        _knowledge_versions(session),
    )
    if draft_type == "LOW_SCORE_DECLINE":
        prior_decline = session.scalar(
            select(db.GeneratedDraft).where(
                db.GeneratedDraft.conversation_id == conversation.id,
                db.GeneratedDraft.draft_type == "LOW_SCORE_DECLINE",
            )
        )
        if prior_decline:
            return _draft_response(session, prior_decline)
    existing = session.scalar(select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint))
    if existing:
        return _draft_response(session, existing)
    if draft_type == "LOW_SCORE_DECLINE":
        result = build_low_score_decline(
            [item.rule_code for item in score.rejections] + score.action_blockers
        )
        message.status = "LOW_SCORE_DECLINED"
    elif blocked:
        result = build_low_score_decline(
            [item.rule_code for item in score.rejections] + score.action_blockers
        )
        result.decision = Decision.DENY
        result.reason_codes = ["JOB_NOT_ELIGIBLE_OR_OPEN"]
    else:
        facts = _knowledge_facts(session)
        recent = session.scalars(
            select(db.Message)
            .where(db.Message.conversation_id == conversation.id)
            .order_by(db.Message.received_at.desc())
            .limit(20)
        ).all()
        classification = _call_llm(
            session,
            llm_provider,
            "MESSAGE_CLASSIFY",
            "classify_message",
            _fingerprint(message.id, message.content),
            lambda: llm_provider.classify_message(
                MessageClassificationRequest(
                    message=message.content,
                    recent_messages=[item.content for item in reversed(recent)],
                )
            ),
        ).data
        message.intents = [intent.value for intent in classification.intents]
        usable = [
            fact
            for fact in facts
            if fact.id
            and fact.allowed_for_auto_reply
            and fact.sensitivity.value == "NORMAL"
            and fact.is_current(datetime.now(UTC))
        ]
        generated: GeneratedMessage | None = None
        if usable and not {"SENSITIVE", "INTERVIEW_TIME"}.intersection(message.intents):
            generated = _call_llm(
                session,
                llm_provider,
                "REPLY",
                "generate_reply",
                _fingerprint(message.id, [str(fact.id) for fact in usable]),
                lambda: llm_provider.generate_reply(
                    LlmReplyRequest(
                        incoming_message=message.content,
                        recent_messages=[item.content for item in reversed(recent)],
                        facts=[
                            TrustedFact(id=fact.id, content=fact.fact)
                            for fact in usable
                            if fact.id
                        ],
                    )
                ),
            ).data
        result = build_llm_reply(
            classification,
            generated,
            facts,
            get_conversation_policy(),
            now=datetime.now(UTC),
        )
    return _persist_draft(
        session,
        result,
        fingerprint,
        draft_type,
        conversation.id,
        message.id,
        score.id,
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
    fingerprint = _fingerprint("GREETING", score.id, score.input_fingerprint, _knowledge_versions(session))
    existing = session.scalar(select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint))
    if existing:
        return _draft_response(session, existing)
    llm_provider = provider or build_llm_provider(get_settings())
    facts = _knowledge_facts(session)
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
                matched_skills=(parsed.required_skills + parsed.preferred_skills)[:5],
                facts=[
                    TrustedFact(id=fact.id, content=fact.fact)
                    for fact in usable
                    if fact.id
                ],
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
    if (
        score.hard_rejected
        or score.effective_job_status != "OPEN"
        or not score.automation_eligible
    ):
        result.decision = Decision.DENY
        result.reason_codes = ["JOB_NOT_ELIGIBLE_OR_OPEN"]
    return _persist_draft(session, result, fingerprint, "GREETING", None, None, score.id)


def create_resume_draft(
    session: Session,
    message_id: object,
    provider: LlmProvider | None = None,
) -> DraftResponse:
    message = session.get(db.Message, message_id)
    if message is None:
        raise ResourceNotFoundError("消息不存在")
    conversation = _get_conversation(session, message.conversation_id)
    llm_provider = provider or build_llm_provider(get_settings())
    score = _bind_current_score(session, conversation, llm_provider)
    messages = session.scalars(
        select(db.Message)
        .where(
            db.Message.conversation_id == conversation.id,
            db.Message.direction == "INBOUND",
        )
        .order_by(db.Message.received_at.asc())
    ).all()
    evaluation = _call_llm(
        session,
        llm_provider,
        "CONVERSATION_EVALUATE",
        "evaluate_conversation",
        _fingerprint(conversation.id, [(item.id, item.content) for item in messages]),
        lambda: llm_provider.evaluate_conversation(
            ConversationEvaluationRequest(
                messages=[
                    ConversationMessage(id=item.id, content=item.content)
                    for item in messages
                ]
            )
        ),
    ).data
    valid_message_ids = {item.id for item in messages}
    evidence_valid = has_valid_conversation_evidence(evaluation, valid_message_ids)
    resumes = session.scalars(
        select(db.Resume).where(
            db.Resume.user_id == DEFAULT_USER_ID,
            db.Resume.platform == conversation.platform,
            db.Resume.is_available.is_(True),
        )
    ).all()
    job = get_job_entity(session, conversation.job_id)
    selected = select_resume(
        [
            ResumeCandidate(
                id=item.id,
                attachment_name=item.attachment_name,
                target_directions=item.target_directions,
                is_available=item.is_available,
            )
            for item in resumes
        ],
        job.title,
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
        score.total_score >= 60
        and not score.hard_rejected
        and score.effective_job_status == "OPEN"
        and evidence_valid
        and (evaluation.resume_requested or evaluation.positive_feedback)
        and selected is not None
        and not duplicate
    )
    fingerprint = _fingerprint(
        "RESUME",
        conversation.id,
        selected.id if selected else None,
        evaluation.evidence_message_ids,
    )
    existing = session.scalar(
        select(db.GeneratedDraft).where(db.GeneratedDraft.input_fingerprint == fingerprint)
    )
    if existing:
        return _draft_response(session, existing)
    result = DraftResult(
        content=selected.attachment_name if selected else "无可用的匹配简历附件",
        intents=[Intent.RESUME_REQUEST],
        confidence=float(evaluation.confidence),
        risk_codes=[] if allowed else ["RESUME_SEND_CONDITIONS_NOT_MET"],
        decision=Decision.ALLOW_AUTO if allowed else Decision.DENY,
        reason_codes=["RESUME_SEND_ALLOWED"] if allowed else ["RESUME_SEND_DENIED"],
    )
    return _persist_draft(
        session,
        result,
        fingerprint,
        "RESUME",
        conversation.id,
        message.id,
        score.id,
        resume_id=selected.id if selected else None,
    )


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
    resume_id: object | None = None,
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
                        "confidence": result.confidence, "risk_codes": result.risk_codes,
                        "resume_id": str(resume_id) if resume_id else None},
    )
    session.add(decision)
    session.flush()
    if decision.decision == "REQUIRE_CONFIRMATION":
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
                         confirmation_task_id=task.id if task else None,
                         resume_id=(
                             decision.input_snapshot.get("resume_id")
                             if decision.input_snapshot.get("resume_id")
                             else None
                         ))


def _knowledge_facts(session: Session) -> list[KnowledgeFact]:
    return [KnowledgeFact(id=item.id, category=item.category, key=item.key, fact=item.fact,
                          source=item.source, allowed_for_auto_reply=item.allowed_for_auto_reply,
                          sensitivity=item.sensitivity, verified_at=item.verified_at,
                          valid_until=item.valid_until, version=item.version)
            for item in get_knowledge_entities(session)]


def _knowledge_versions(session: Session) -> list[tuple[str, int]]:
    return sorted((str(item.id), item.version) for item in get_knowledge_entities(session))


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
