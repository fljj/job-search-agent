from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.services.conversation_service import (
    _full_time_education_reply,
    _is_ignored_platform_event,
    _relevant_reply_facts,
    _safe_job_detail_clarification,
)
from packages.conversation_agent.generator import generate_greeting, generate_reply
from packages.conversation_agent.intents import (
    classify_intents,
    is_explicit_resume_request,
    normalize_intents,
)
from packages.conversation_agent.models import ConversationPolicyConfig, Decision, Intent
from packages.knowledge_base.models import KnowledgeFact
from packages.llm.models import ConversationMemory


def fact(**changes: object) -> KnowledgeFact:
    values: dict[str, object] = {
        "id": uuid4(),
        "category": "TECH_STACK",
        "key": "Java",
        "fact": "Java 后端开发经验 8 年",
        "source": "用户确认",
        "allowed_for_auto_reply": True,
        "sensitivity": "NORMAL",
        "verified_at": datetime.now(UTC),
        "version": 1,
    }
    values.update(changes)
    return KnowledgeFact.model_validate(values)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("你的 Java 技术栈经验怎么样？", Intent.TECH_STACK),
        ("可以发一份简历吗？", Intent.RESUME_REQUEST),
        ("周二几点可以电话面试？", Intent.INTERVIEW_TIME),
        ("请提供身份证号", Intent.SENSITIVE),
        ("请问你的本科是全日制吗？", Intent.EDUCATION),
    ],
)
def test_classify_intents(content: str, expected: Intent) -> None:
    assert expected in classify_intents(content)


def test_arrival_date_is_not_misclassified_as_interview_time() -> None:
    assert classify_intents("最快到岗时间是多久？") == [Intent.ARRIVAL_DATE]
    assert normalize_intents(
        "最快到岗时间是多久？",
        [Intent.ARRIVAL_DATE, Intent.INTERVIEW_TIME],
    ) == [Intent.ARRIVAL_DATE]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("麻烦发送一份简历", True),
        ("方便发简历吗", True),
        ("我已经看过简历了", False),
        ("简历不太合适", False),
        ("简历里没看到项目经验", False),
        ("不用发简历", False),
    ],
)
def test_explicit_resume_request_requires_current_positive_request(
    content: str,
    expected: bool,
) -> None:
    assert is_explicit_resume_request(content) is expected
    assert (Intent.RESUME_REQUEST in classify_intents(content)) is expected


def test_generic_conversation_cannot_be_upgraded_to_phone_or_interview() -> None:
    assert normalize_intents(
        "你好，看过了你的简历，希望和你交流一下",
        [Intent.PHONE_CALL, Intent.INTERVIEW_INVITATION],
    ) == [Intent.UNCLEAR]


def test_explicit_phone_or_interview_evidence_keeps_privileged_intent() -> None:
    assert normalize_intents(
        "方便电话沟通一下吗？",
        [Intent.PHONE_CALL],
    ) == [Intent.PHONE_CALL]
    assert normalize_intents(
        "想邀请你参加面试",
        [Intent.INTERVIEW_INVITATION],
    ) == [Intent.INTERVIEW_INVITATION]


def test_verified_normal_fact_allows_automatic_reply() -> None:
    result = generate_reply("请介绍 Java 技术栈", [fact()], ConversationPolicyConfig())
    assert result.decision is Decision.ALLOW_AUTO
    assert "8 年" in result.content
    assert result.confidence >= 0.9


def test_missing_fact_never_invents_answer() -> None:
    result = generate_reply("你做过什么区块链项目？", [], ConversationPolicyConfig())
    assert result.decision is Decision.ALLOW_AUTO
    assert result.content == "这部分信息我需要确认一下，稍后回复您。"


def test_reply_facts_are_filtered_by_llm_classified_intent() -> None:
    employment = fact(category="EMPLOYMENT", key="最近一份工作", fact="最近在交易所工作")
    project = fact(category="PROJECT", key="认证平台", fact="参与 OAuth2 平台建设")

    selected = _relevant_reply_facts(
        [project, employment], [Intent.WORK_EXPERIENCE]
    )

    assert selected == [employment]


def test_sensitive_question_uses_safe_automatic_path() -> None:
    result = generate_reply("请提供身份证号", [fact()], ConversationPolicyConfig())
    assert result.decision is Decision.ALLOW_AUTO


def test_interview_time_always_requires_confirmation() -> None:
    result = generate_reply("周二几点可以电话面试？", [fact()], ConversationPolicyConfig())
    assert result.decision is Decision.REQUIRE_CONFIRMATION


def test_fact_not_allowed_for_auto_reply_is_denied_without_confirmation() -> None:
    result = generate_reply(
        "请介绍 Java 技术栈", [fact(allowed_for_auto_reply=False)], ConversationPolicyConfig()
    )
    assert result.decision is Decision.DENY


def test_expired_fact_is_treated_as_missing() -> None:
    expired = fact(valid_until=datetime.now(UTC) - timedelta(days=1))
    result = generate_reply("请介绍 Java 技术栈", [expired], ConversationPolicyConfig())
    assert result.reason_codes == ["SAFE_MISSING_FACT_REPLY"]


def test_greetings_differ_by_job_and_use_verified_fact() -> None:
    config = ConversationPolicyConfig()
    java_fact = fact()
    cloud_fact = fact(key="Kubernetes", fact="拥有 Kubernetes 生产集群维护经验")
    java = generate_greeting(
        "Java后端", "甲公司", "电商", ["Java"], [java_fact, cloud_fact], config
    )
    cloud = generate_greeting(
        "云平台", "乙公司", "云计算", ["Kubernetes"], [java_fact, cloud_fact], config
    )
    assert java.content != cloud.content
    assert "甲公司" in java.content
    assert java.fact_ids
    assert "Kubernetes" in cloud.content
    assert cloud.decision is Decision.ALLOW_AUTO


class _EducationSession:
    def __init__(self) -> None:
        self.fact_ids = {
            key: uuid4() for key in ("专科学习形式", "本科学习形式", "硕士研究生学习形式")
        }
        self.calls = 0

    def scalars(self, _query: object) -> object:
        self.calls += 1
        facts = [
            type("Fact", (), {"id": fact_id, "normalized_key": key})()
            for key, fact_id in self.fact_ids.items()
        ]
        return type("ScalarResult", (), {"all": lambda _self: facts})()


def test_full_time_education_is_disclosed_only_when_explicitly_asked() -> None:
    session = _EducationSession()
    asked = type("Message", (), {"content": "请问你的本科是全日制吗？"})()

    result = _full_time_education_reply(
        cast(Session, session), cast(db.Message, asked)
    )

    assert result is not None
    assert result.content == "我的本科不是统招、不是全日制，学历可在学信网查询。"
    assert result.fact_ids == [session.fact_ids["本科学习形式"]]


def test_postgraduate_education_reply_uses_verified_knowledge() -> None:
    session = _EducationSession()
    asked = type("Message", (), {"content": "研究生是统招还是在职？学信网可查吗？"})()

    result = _full_time_education_reply(
        cast(Session, session), cast(db.Message, asked)
    )

    assert result is not None
    assert result.content == "我的硕士研究生属于统招，为在职就读，学历可在学信网查询。"
    assert result.fact_ids == [session.fact_ids["硕士研究生学习形式"]]


def test_full_time_education_is_not_proactively_disclosed() -> None:
    session = _EducationSession()
    ordinary = type("Message", (), {"content": "请介绍一下你的项目经验"})()

    assert (
        _full_time_education_reply(
            cast(Session, session), cast(db.Message, ordinary)
        )
        is None
    )
    assert session.calls == 0


def test_resume_viewed_platform_event_does_not_need_a_reply() -> None:
    assert _is_ignored_platform_event(" 对方已查看了您的附件简历 ")
    assert not _is_ignored_platform_event("可以发一份附件简历吗？")


def test_safe_clarification_does_not_repeat_discussed_topics() -> None:
    result = _safe_job_detail_clarification(
        ConversationMemory(
            candidate_asked_topics=["JOB_DETAIL", "SALARY"],
            confirmed_topics=["LOCATION"],
        )
    )

    assert "岗位职责" not in result.content
    assert "薪资" not in result.content
    assert "工作地点" not in result.content
    assert "工作模式" in result.content
