from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.api.app.services.conversation_service import _full_time_education_reply
from packages.conversation_agent.generator import generate_greeting, generate_reply
from packages.conversation_agent.intents import classify_intents
from packages.conversation_agent.models import ConversationPolicyConfig, Decision, Intent
from packages.knowledge_base.models import KnowledgeFact


def fact(**changes: object) -> KnowledgeFact:
    values: dict[str, object] = {
        "id": uuid4(), "category": "TECH_STACK", "key": "Java",
        "fact": "Java 后端开发经验 8 年", "source": "用户确认",
        "allowed_for_auto_reply": True, "sensitivity": "NORMAL",
        "verified_at": datetime.now(UTC), "version": 1,
    }
    values.update(changes)
    return KnowledgeFact.model_validate(values)


@pytest.mark.parametrize(("content", "expected"), [
    ("你的 Java 技术栈经验怎么样？", Intent.TECH_STACK),
    ("可以发一份简历吗？", Intent.RESUME_REQUEST),
    ("周二几点可以电话面试？", Intent.INTERVIEW_TIME),
    ("请提供身份证号", Intent.SENSITIVE),
    ("请问你的本科是全日制吗？", Intent.EDUCATION),
])
def test_classify_intents(content: str, expected: Intent) -> None:
    assert expected in classify_intents(content)


def test_verified_normal_fact_allows_automatic_reply() -> None:
    result = generate_reply("请介绍 Java 技术栈", [fact()], ConversationPolicyConfig())
    assert result.decision is Decision.ALLOW_AUTO
    assert "8 年" in result.content
    assert result.confidence >= 0.9


def test_missing_fact_never_invents_answer() -> None:
    result = generate_reply("你做过什么区块链项目？", [], ConversationPolicyConfig())
    assert result.decision is Decision.ALLOW_AUTO
    assert result.content == "这部分信息我需要确认一下，稍后回复您。"


def test_sensitive_question_uses_safe_automatic_path() -> None:
    result = generate_reply("请提供身份证号", [fact()], ConversationPolicyConfig())
    assert result.decision is Decision.ALLOW_AUTO


def test_interview_time_always_requires_confirmation() -> None:
    result = generate_reply("周二几点可以电话面试？", [fact()], ConversationPolicyConfig())
    assert result.decision is Decision.REQUIRE_CONFIRMATION


def test_fact_not_allowed_for_auto_reply_is_denied_without_confirmation() -> None:
    result = generate_reply("请介绍 Java 技术栈", [fact(allowed_for_auto_reply=False)],
                            ConversationPolicyConfig())
    assert result.decision is Decision.DENY


def test_expired_fact_is_treated_as_missing() -> None:
    expired = fact(valid_until=datetime.now(UTC) - timedelta(days=1))
    result = generate_reply("请介绍 Java 技术栈", [expired], ConversationPolicyConfig())
    assert result.reason_codes == ["SAFE_MISSING_FACT_REPLY"]


def test_greetings_differ_by_job_and_use_verified_fact() -> None:
    config = ConversationPolicyConfig()
    java_fact = fact()
    cloud_fact = fact(key="Kubernetes", fact="拥有 Kubernetes 生产集群维护经验")
    java = generate_greeting("Java后端", "甲公司", "电商", ["Java"], [java_fact, cloud_fact], config)
    cloud = generate_greeting("云平台", "乙公司", "云计算", ["Kubernetes"], [java_fact, cloud_fact], config)
    assert java.content != cloud.content
    assert "甲公司" in java.content
    assert java.fact_ids
    assert "Kubernetes" in cloud.content
    assert cloud.decision is Decision.ALLOW_AUTO


class _EducationSession:
    def __init__(self, bachelor_full_time: bool | None) -> None:
        self.bachelor_full_time = bachelor_full_time
        self.fact_id = uuid4()
        self.calls = 0

    def scalar(self, _query: object) -> object:
        self.calls += 1
        if self.calls == 1:
            return type(
                "Profile",
                (),
                {"bachelor_full_time": self.bachelor_full_time},
            )()
        return type("Fact", (), {"id": self.fact_id})()


def test_full_time_education_is_disclosed_only_when_explicitly_asked() -> None:
    session = _EducationSession(False)
    asked = type("Message", (), {"content": "请问你的本科是全日制吗？"})()

    result = _full_time_education_reply(session, asked)  # type: ignore[arg-type]

    assert result is not None
    assert result.content == "我的本科不是全日制，供您确认是否符合岗位要求。"
    assert result.fact_ids == [session.fact_id]


def test_full_time_education_is_not_proactively_disclosed() -> None:
    session = _EducationSession(False)
    ordinary = type("Message", (), {"content": "请介绍一下你的项目经验"})()

    assert _full_time_education_reply(  # type: ignore[arg-type]
        session, ordinary
    ) is None
    assert session.calls == 0
