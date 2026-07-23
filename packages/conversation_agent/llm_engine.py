from datetime import datetime
from uuid import UUID

from packages.conversation_agent.models import (
    ConversationPolicyConfig,
    Decision,
    DraftResult,
    Intent,
)
from packages.conversation_agent.policy import decide_reply
from packages.knowledge_base.models import KnowledgeFact, Sensitivity
from packages.llm.models import ConversationEvaluation, GeneratedMessage, MessageClassification
from packages.policy_engine.content_check import validate_edited_content

SAFE_SENSITIVE_REPLY = "抱歉，这类敏感信息不便提供，我们可以继续在招聘平台沟通。"
SAFE_TIME_REPLY = "收到您的时间安排邀请，具体时间需要确认后再回复您。"


def build_llm_reply(
    classification: MessageClassification,
    generated: GeneratedMessage | None,
    facts: list[KnowledgeFact],
    config: ConversationPolicyConfig,
    *,
    now: datetime,
) -> DraftResult:
    intents = classification.intents
    if Intent.INTERVIEW_TIME in intents:
        return DraftResult(
            content=SAFE_TIME_REPLY,
            intents=intents,
            confidence=float(classification.confidence),
            decision=Decision.REQUIRE_CONFIRMATION,
            reason_codes=["SPECIFIC_TIME_REQUIRES_CONFIRMATION"],
        )
    if Intent.SENSITIVE in intents:
        return DraftResult(
            content=SAFE_SENSITIVE_REPLY,
            intents=intents,
            confidence=float(classification.confidence),
            risk_codes=["SENSITIVE_REQUEST_REFUSED"],
            decision=Decision.ALLOW_AUTO,
            reason_codes=["SAFE_SENSITIVE_REFUSAL"],
        )

    usable = {
        fact.id: fact
        for fact in facts
        if fact.id
        and fact.allowed_for_auto_reply
        and fact.sensitivity is Sensitivity.NORMAL
        and fact.is_current(now)
    }
    if generated is None or not usable or classification.confidence < config.confirmation_min_confidence:
        confidence = min(float(classification.confidence), 0.74)
        return DraftResult(
            content=config.missing_fact_reply,
            intents=intents,
            confidence=confidence,
            risk_codes=["MISSING_OR_LOW_CONFIDENCE_FACTS"],
            decision=Decision.ALLOW_AUTO,
            reason_codes=["SAFE_CONSERVATIVE_REPLY"],
        )
    if any(fact_id not in usable for fact_id in generated.fact_ids):
        return DraftResult(
            content=config.missing_fact_reply,
            intents=intents,
            confidence=0,
            risk_codes=["UNTRUSTED_FACT_REFERENCE"],
            decision=Decision.DENY,
            reason_codes=["LLM_REFERENCED_UNKNOWN_FACT"],
        )
    if validate_edited_content(generated.content):
        return DraftResult(
            content=config.missing_fact_reply,
            intents=intents,
            confidence=0,
            risk_codes=["UNSAFE_GENERATED_CONTENT"],
            decision=Decision.DENY,
            reason_codes=["LLM_CONTENT_REJECTED"],
        )
    referenced = [usable[fact_id] for fact_id in generated.fact_ids]
    confidence = min(float(classification.confidence), float(generated.confidence))
    decision, reasons = decide_reply(intents, referenced, confidence, config)
    return DraftResult(
        content=generated.content,
        intents=intents,
        fact_ids=generated.fact_ids,
        confidence=confidence,
        risk_codes=generated.risk_codes,
        decision=decision,
        reason_codes=reasons,
    )


def build_low_score_decline(reason_codes: list[str]) -> DraftResult:
    public_reasons: list[str] = []
    if any("LOCATION" in code or "WORK_MODE" in code for code in reason_codes):
        public_reasons.append("工作地点或工作模式与当前计划不太一致")
    if any("SALARY" in code for code in reason_codes):
        public_reasons.append("薪资范围与当前考虑范围存在差异")
    if not public_reasons:
        public_reasons.append("岗位方向和当前求职计划不完全匹配")
    reason = "，".join(public_reasons[:2])
    return DraftResult(
        content=f"感谢您的联系。综合考虑后，{reason}，这次先不继续沟通了，祝招聘顺利。",
        intents=[Intent.JOB_DETAIL],
        confidence=1,
        risk_codes=["LOW_SCORE_DECLINE"],
        decision=Decision.ALLOW_AUTO,
        reason_codes=["LOW_SCORE_DECLINE_ALLOWED"],
    )


def has_valid_conversation_evidence(
    evaluation: ConversationEvaluation,
    message_ids: set[UUID],
) -> bool:
    return bool(evaluation.evidence_message_ids) and all(
        message_id in message_ids for message_id in evaluation.evidence_message_ids
    )
