from packages.conversation_agent.models import Decision, DraftResult, Intent

LOCATION_TERMS = ("工作地点", "办公地点", "哪个城市", "哪些城市")


def build_location_reply(content: str, onsite_locations: list[str]) -> DraftResult | None:
    asks_known_city = any(location in content for location in onsite_locations) and any(
        term in content for term in ("是否接受", "能否接受", "可以去", "考虑")
    )
    if not asks_known_city and not any(term in content for term in LOCATION_TERMS):
        return None
    if not onsite_locations:
        return None
    locations = "、".join(dict.fromkeys(onsite_locations))
    return DraftResult(
        content=f"现场办公地点目前只考虑{locations}，其他地点暂不考虑。",
        intents=[Intent.LOCATION],
        confidence=1,
        decision=Decision.ALLOW_AUTO,
        reason_codes=["CONFIGURED_LOCATION_POLICY_REPLY"],
    )
