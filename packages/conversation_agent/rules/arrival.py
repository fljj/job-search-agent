from packages.conversation_agent.models import Decision, DraftResult, Intent

DEFAULT_ARRIVAL_REPLY = "我最快可以在一周内到岗，具体日期可以结合双方安排确认。"
ARRIVAL_TERMS = ("最快多久到岗", "最快到岗", "什么时候可以入职", "何时能开始工作", "多久能入职")


def build_arrival_reply(content: str, configured_reply: str | None) -> DraftResult | None:
    if not any(term in content for term in ARRIVAL_TERMS):
        return None
    return DraftResult(
        content=configured_reply or DEFAULT_ARRIVAL_REPLY,
        intents=[Intent.ARRIVAL_DATE],
        confidence=1,
        decision=Decision.ALLOW_AUTO,
        reason_codes=["CONFIGURED_ARRIVAL_TIME_REPLY"],
    )
