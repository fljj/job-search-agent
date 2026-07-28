from packages.conversation_agent.models import Decision, DraftResult, Intent

WORK_MODE_TERMS = ("是否接受远程", "能否远程", "接受远程", "混合办公", "现场办公", "onsite", "hybrid")
MODE_LABELS = {"REMOTE": "远程", "ONSITE": "现场办公", "HYBRID": "混合办公"}


def build_work_mode_reply(content: str, enabled_modes: list[str]) -> DraftResult | None:
    if not any(term in content.lower() for term in WORK_MODE_TERMS) or not enabled_modes:
        return None
    labels = "、".join(
        MODE_LABELS.get(mode, mode) for mode in dict.fromkeys(enabled_modes)
    )
    return DraftResult(
        content=f"我目前接受的工作模式是{labels}，具体安排可以进一步沟通。",
        intents=[Intent.REMOTE_POLICY],
        confidence=1,
        decision=Decision.ALLOW_AUTO,
        reason_codes=["CONFIGURED_WORK_MODE_POLICY_REPLY"],
    )
