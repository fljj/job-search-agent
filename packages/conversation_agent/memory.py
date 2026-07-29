import re

from packages.conversation_agent.intents import classify_intents
from packages.conversation_agent.models import Intent
from packages.llm.models import ConversationMemory, ConversationTurn

QUESTION_MARKERS = ("？", "?", "请问", "方便", "想了解", "确认一下")
IGNORED_MEMORY_INTENTS = {
    Intent.UNCLEAR,
    Intent.SENSITIVE,
    Intent.INTERVIEW_TIME,
}


def build_conversation_memory(
    turns: list[ConversationTurn],
    *,
    completed_actions: list[str] | None = None,
) -> ConversationMemory:
    """从有方向的原始消息确定性提取已讨论主题和未决问题。"""
    discussed: set[str] = set()
    asked: set[str] = set()
    confirmed: set[str] = set()
    confirmed_details: dict[str, str] = {}
    open_topics: set[str] = set()
    for turn in turns:
        topics = {
            intent.value
            for intent in classify_intents(turn.content)
            if intent not in IGNORED_MEMORY_INTENTS
        }
        discussed.update(topics)
        question = _is_question(turn.content)
        if turn.direction == "OUTBOUND" and question:
            asked.update(topics)
            open_topics.update(topics)
        elif turn.direction == "INBOUND" and not question:
            confirmed.update(topics)
            for topic in topics:
                confirmed_details[topic] = turn.content[:500]
            open_topics.difference_update(topics)
    return ConversationMemory(
        discussed_topics=sorted(discussed),
        candidate_asked_topics=sorted(asked),
        confirmed_topics=sorted(confirmed),
        confirmed_details=confirmed_details,
        open_questions=sorted(open_topics),
        completed_actions=sorted(set(completed_actions or [])),
    )


def remove_repeated_questions(
    content: str,
    memory: ConversationMemory,
) -> tuple[str, list[str]]:
    """移除模型生成的、此前已经问过或已经确认答案的问句。"""
    blocked_topics = set(memory.candidate_asked_topics) | set(memory.confirmed_topics)
    if not blocked_topics:
        return content, []
    kept: list[str] = []
    repeated: set[str] = set()
    for sentence in _sentences(content):
        topics = {
            intent.value
            for intent in classify_intents(sentence)
            if intent not in IGNORED_MEMORY_INTENTS
        }
        duplicated = topics & blocked_topics
        if _is_question(sentence) and duplicated:
            repeated.update(duplicated)
            continue
        kept.append(sentence)
    cleaned = "".join(kept).strip()
    if not cleaned:
        cleaned = "感谢您的补充，我已经了解这些信息，后续可以继续沟通。"
    return cleaned, sorted(repeated)


def _is_question(content: str) -> bool:
    return any(marker in content for marker in QUESTION_MARKERS)


def _sentences(content: str) -> list[str]:
    return [sentence for sentence in re.split(r"(?<=[。！？!?])", content) if sentence.strip()]
