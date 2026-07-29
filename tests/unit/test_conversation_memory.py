from packages.conversation_agent.memory import (
    build_conversation_memory,
    remove_repeated_questions,
)
from packages.llm.models import ConversationTurn


def turn(direction: str, content: str) -> ConversationTurn:
    return ConversationTurn(direction=direction, content=content)


def test_memory_distinguishes_candidate_questions_and_recruiter_answers() -> None:
    memory = build_conversation_memory(
        [
            turn("OUTBOUND", "请问这个岗位的薪资范围是多少？"),
            turn("INBOUND", "薪资范围是25K到30K。"),
            turn("OUTBOUND", "方便介绍一下岗位职责吗？"),
        ],
        completed_actions=["RESUME"],
    )

    assert memory.candidate_asked_topics == ["JOB_DETAIL", "SALARY"]
    assert memory.confirmed_topics == ["SALARY"]
    assert memory.confirmed_details == {"SALARY": "薪资范围是25K到30K。"}
    assert memory.open_questions == ["JOB_DETAIL"]
    assert memory.completed_actions == ["RESUME"]


def test_repeated_question_is_removed_but_new_question_is_kept() -> None:
    memory = build_conversation_memory(
        [
            turn("OUTBOUND", "请问薪资范围是多少？"),
            turn("INBOUND", "薪资是25K到30K。"),
        ]
    )

    content, repeated = remove_repeated_questions(
        "感谢您的回复。请问薪资范围还有调整空间吗？这个岗位支持远程办公吗？",
        memory,
    )

    assert content == "感谢您的回复。这个岗位支持远程办公吗？"
    assert repeated == ["SALARY"]


def test_only_repeated_question_uses_safe_acknowledgement() -> None:
    memory = build_conversation_memory([turn("OUTBOUND", "方便介绍一下岗位职责吗？")])

    content, repeated = remove_repeated_questions(
        "方便再介绍一下岗位职责吗？",
        memory,
    )

    assert content == "感谢您的补充，我已经了解这些信息，后续可以继续沟通。"
    assert repeated == ["JOB_DETAIL"]
