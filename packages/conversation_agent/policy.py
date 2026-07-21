from packages.conversation_agent.models import ConversationPolicyConfig, Decision, Intent
from packages.knowledge_base.models import KnowledgeFact, Sensitivity

MANDATORY_CONFIRMATION = {
    Intent.SALARY, Intent.ARRIVAL_DATE, Intent.PHONE_CALL,
    Intent.INTERVIEW_INVITATION, Intent.INTERVIEW_TIME,
}


def decide_reply(
    intents: list[Intent], facts: list[KnowledgeFact], confidence: float,
    config: ConversationPolicyConfig,
) -> tuple[Decision, list[str]]:
    if Intent.SENSITIVE in intents or any(fact.sensitivity is Sensitivity.PROHIBITED for fact in facts):
        return Decision.DENY, ["SENSITIVE_OR_PROHIBITED"]
    if MANDATORY_CONFIRMATION.intersection(intents):
        return Decision.REQUIRE_CONFIRMATION, ["MANDATORY_CONFIRMATION_INTENT"]
    if not facts:
        return Decision.REQUIRE_CONFIRMATION, ["MISSING_VERIFIED_FACT"]
    if any(fact.sensitivity is Sensitivity.SENSITIVE for fact in facts):
        return Decision.REQUIRE_CONFIRMATION, ["SENSITIVE_FACT"]
    if not all(fact.allowed_for_auto_reply for fact in facts):
        return Decision.REQUIRE_CONFIRMATION, ["FACT_NOT_ALLOWED_FOR_AUTO_REPLY"]
    if confidence < config.confirmation_min_confidence:
        return Decision.REQUIRE_CONFIRMATION, ["LOW_CONFIDENCE"]
    if confidence < config.auto_reply_min_confidence:
        return Decision.REQUIRE_CONFIRMATION, ["MEDIUM_CONFIDENCE"]
    return Decision.ALLOW_AUTO, ["VERIFIED_FACTS_AND_HIGH_CONFIDENCE"]
