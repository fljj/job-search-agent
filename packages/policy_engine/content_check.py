from packages.conversation_agent.intents import classify_intents
from packages.conversation_agent.models import Intent

PROHIBITED_TERMS = ("身份证", "银行卡", "密码", "验证码", "客户名单")


def validate_edited_content(content: str) -> list[str]:
    if not content.strip():
        return ["EMPTY_CONTENT"]
    if Intent.SENSITIVE in classify_intents(content) or any(term in content for term in PROHIBITED_TERMS):
        return ["SENSITIVE_OR_PROHIBITED"]
    return []
