from datetime import UTC, datetime

from packages.conversation_agent.intents import classify_intents
from packages.conversation_agent.models import ConversationPolicyConfig, DraftResult, Intent
from packages.conversation_agent.policy import decide_reply
from packages.knowledge_base.models import KnowledgeFact
from packages.knowledge_base.retrieval import retrieve_facts

GENERATOR_VERSION = "conversation-rules-v1"


def generate_reply(
    content: str, facts: list[KnowledgeFact], config: ConversationPolicyConfig,
    now: datetime | None = None,
) -> DraftResult:
    current = now or datetime.now(UTC)
    intents = classify_intents(content)
    terms = [intent.value for intent in intents] + content.split()
    matched = retrieve_facts(facts, terms, current)
    risks: list[str] = []
    if not matched:
        risks.append("MISSING_VERIFIED_FACT")
        draft = config.missing_fact_reply
        confidence = 0.4
    else:
        draft = "；".join(item.fact.rstrip("。；") for item in matched[:3]) + "。"
        freshness = sum(1 for item in matched if item.is_current(current)) / len(matched)
        confidence = min(0.98, 0.75 + 0.15 * freshness + min(len(matched), 2) * 0.04)
    decision, reasons = decide_reply(intents, matched, confidence, config)
    return DraftResult(content=draft, intents=intents,
                       fact_ids=[item.id for item in matched if item.id],
                       confidence=confidence, risk_codes=risks,
                       decision=decision, reason_codes=reasons)


def generate_greeting(
    job_title: str, company_name: str, industry: str | None,
    skills: list[str], facts: list[KnowledgeFact], config: ConversationPolicyConfig,
) -> DraftResult:
    matched = retrieve_facts(facts, skills, datetime.now(UTC))[:config.max_greeting_facts]
    skill_text = "、".join(skills[:config.max_greeting_skills])
    fact_text = "；".join(item.fact.rstrip("。；") for item in matched)
    context = f"贵司{industry or ''}{job_title}岗位"
    content = f"您好，我关注到{company_name}的{context}，岗位重点与{skill_text or '相关技术'}有关。"
    if fact_text:
        content += f"我的相关经验包括：{fact_text}。想进一步了解团队和业务方向。"
        confidence = 0.95
    else:
        content = config.missing_fact_reply
        confidence = 0.4
    decision, reasons = decide_reply([Intent.JOB_DETAIL], matched, confidence, config)
    return DraftResult(content=content, intents=[Intent.JOB_DETAIL],
                       fact_ids=[item.id for item in matched if item.id], confidence=confidence,
                       risk_codes=[] if matched else ["MISSING_VERIFIED_FACT"],
                       decision=decision, reason_codes=reasons)
