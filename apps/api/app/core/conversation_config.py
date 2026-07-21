import json
from functools import lru_cache
from pathlib import Path

from packages.conversation_agent.models import ConversationPolicyConfig


@lru_cache
def get_conversation_policy() -> ConversationPolicyConfig:
    path = Path(__file__).resolve().parents[4] / "config" / "conversation-policy.json"
    return ConversationPolicyConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
