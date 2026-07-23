from packages.conversation_agent.models import ConversationPolicyConfig, Decision, Intent
from packages.knowledge_base.models import KnowledgeFact, Sensitivity

MANDATORY_CONFIRMATION = {Intent.INTERVIEW_TIME}


def decide_reply(
    intents: list[Intent], facts: list[KnowledgeFact], confidence: float,
    config: ConversationPolicyConfig,
) -> tuple[Decision, list[str]]:
    if MANDATORY_CONFIRMATION.intersection(intents):
        return Decision.REQUIRE_CONFIRMATION, ["SPECIFIC_TIME_REQUIRES_CONFIRMATION"]
    if Intent.SENSITIVE in intents or any(fact.sensitivity is Sensitivity.PROHIBITED for fact in facts):
        return Decision.ALLOW_AUTO, ["SAFE_SENSITIVE_REFUSAL"]
    if not facts:
        return Decision.ALLOW_AUTO, ["SAFE_MISSING_FACT_REPLY"]
    if any(fact.sensitivity is Sensitivity.SENSITIVE for fact in facts):
        return Decision.DENY, ["SENSITIVE_FACT_NOT_USABLE"]
    if not all(fact.allowed_for_auto_reply for fact in facts):
        return Decision.DENY, ["FACT_NOT_ALLOWED_FOR_AUTO_REPLY"]
    if confidence < config.confirmation_min_confidence:
        return Decision.ALLOW_AUTO, ["SAFE_LOW_CONFIDENCE_REPLY"]
    if confidence < config.auto_reply_min_confidence:
        return Decision.ALLOW_AUTO, ["CONSERVATIVE_REPLY"]
    return Decision.ALLOW_AUTO, ["VERIFIED_FACTS_AND_HIGH_CONFIDENCE"]
