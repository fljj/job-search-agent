from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from packages.conversation_agent.llm_engine import (
    SAFE_SENSITIVE_REPLY,
    build_llm_reply,
    build_mismatch_decline,
    has_valid_conversation_evidence,
)
from packages.conversation_agent.models import ConversationPolicyConfig, Decision, Intent
from packages.knowledge_base.models import KnowledgeFact
from packages.llm.models import ConversationEvaluation, GeneratedMessage, MessageClassification


def fact(**changes: object) -> KnowledgeFact:
    values: dict[str, object] = {
        "id": uuid4(),
        "category": "TECH_STACK",
        "key": "Java",
        "fact": "拥有八年 Java 后端经验",
        "source": "用户确认",
        "allowed_for_auto_reply": True,
        "sensitivity": "NORMAL",
        "verified_at": datetime.now(UTC),
        "version": 1,
    }
    values.update(changes)
    return KnowledgeFact.model_validate(values)


def classification(*intents: Intent, confidence: str = "1") -> MessageClassification:
    return MessageClassification(intents=list(intents), confidence=Decimal(confidence))


def test_generated_reply_can_only_reference_trusted_current_facts() -> None:
    trusted = fact()
    generated = GeneratedMessage(
        content="我有八年 Java 后端经验。",
        fact_ids=[trusted.id],
        confidence=Decimal("0.98"),
    )
    result = build_llm_reply(
        classification(Intent.TECH_STACK),
        generated,
        [trusted],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.ALLOW_AUTO
    assert result.fact_ids == [trusted.id]


def test_unknown_or_expired_fact_reference_is_denied() -> None:
    expired = fact(valid_until=datetime.now(UTC) - timedelta(days=1))
    generated = GeneratedMessage(
        content="我有相关经验。",
        fact_ids=[uuid4()],
        confidence=Decimal("1"),
    )
    result = build_llm_reply(
        classification(Intent.TECH_STACK),
        generated,
        [expired],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.ALLOW_AUTO
    assert result.fact_ids == []
    assert "MISSING_OR_LOW_CONFIDENCE_FACTS" in result.risk_codes


def test_forged_fact_id_is_denied_when_other_trusted_facts_exist() -> None:
    trusted = fact()
    generated = GeneratedMessage(
        content="我有相关经验。",
        fact_ids=[uuid4()],
        confidence=Decimal("1"),
    )
    result = build_llm_reply(
        classification(Intent.TECH_STACK),
        generated,
        [trusted],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.DENY
    assert result.reason_codes == ["LLM_REFERENCED_UNKNOWN_FACT"]


def test_unsafe_generated_content_is_denied() -> None:
    trusted = fact()
    generated = GeneratedMessage(
        content="我的银行卡信息可以直接发送。",
        fact_ids=[trusted.id],
        confidence=Decimal("1"),
    )
    result = build_llm_reply(
        classification(Intent.TECH_STACK),
        generated,
        [trusted],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.DENY


def test_sensitive_request_uses_fixed_refusal_without_confirmation() -> None:
    result = build_llm_reply(
        classification(Intent.SENSITIVE),
        None,
        [],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.ALLOW_AUTO
    assert result.content == SAFE_SENSITIVE_REPLY


def test_only_specific_time_path_requires_confirmation() -> None:
    result = build_llm_reply(
        classification(Intent.PHONE_CALL, Intent.INTERVIEW_TIME),
        None,
        [],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.REQUIRE_CONFIRMATION


def test_low_confidence_uses_safe_template_without_confirmation() -> None:
    result = build_llm_reply(
        classification(Intent.UNCLEAR, confidence="0.5"),
        None,
        [],
        ConversationPolicyConfig(),
        now=datetime.now(UTC),
    )
    assert result.decision is Decision.ALLOW_AUTO
    assert result.content == ConversationPolicyConfig().missing_fact_reply


def test_mismatch_decline_hides_internal_blacklist() -> None:
    result = build_mismatch_decline(
        ["COMPANY_BLACKLISTED", "SALARY_BELOW_CONTACT_THRESHOLD"]
    )
    assert result.decision is Decision.ALLOW_AUTO
    assert "59" not in result.content
    assert "黑名单" not in result.content
    assert "薪资范围" in result.content


def test_mismatch_decline_prefers_job_direction_over_salary() -> None:
    result = build_mismatch_decline(
        ["JOB_DIRECTION_CONFLICT", "SALARY_BELOW_CONTACT_THRESHOLD"]
    )

    assert "岗位方向" in result.content
    assert "薪资范围" not in result.content


def test_forged_conversation_message_id_is_rejected() -> None:
    actual_id = uuid4()
    evaluation = ConversationEvaluation(
        resume_requested=True,
        positive_feedback=False,
        evidence_message_ids=[uuid4()],
        confidence=Decimal("1"),
    )
    assert has_valid_conversation_evidence(evaluation, {actual_id}) is False
