from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from packages.conversation_agent.knowledge.profile_answer import CandidateKnowledge
from packages.conversation_agent.models import ReplySource
from packages.conversation_agent.router import ReplyRouteContext, route_reply
from packages.conversation_agent.rules.salary import SalaryExpectation
from packages.knowledge_base.models import KnowledgeFact


def _knowledge() -> CandidateKnowledge:
    project = KnowledgeFact(
        id=uuid4(),
        category="PROJECT",
        key="oauth2",
        fact="参与统一认证 OAuth2 平台建设",
        source="candidate_profile",
        allowed_for_auto_reply=True,
        sensitivity="NORMAL",
        verified_at=datetime.now(UTC),
        version=1,
    )
    return CandidateKnowledge(
        name="测试候选人",
        total_years=Decimal("10"),
        management_years=Decimal("2"),
        profile_id=uuid4(),
        skills=[(uuid4(), "Java", Decimal("8"))],
        facts=[project],
    )


def _context() -> ReplyRouteContext:
    return ReplyRouteContext(
        arrival_time_reply="我最快可以一周内到岗。",
        salary_expectations=[
            SalaryExpectation("REMOTE", "CNY", Decimal("25")),
            SalaryExpectation("ONSITE", "CNY", Decimal("15")),
        ],
        onsite_locations=["济南"],
        enabled_work_modes=["REMOTE", "ONSITE"],
        candidate_knowledge=_knowledge(),
    )


def test_arrival_rule_has_priority_over_knowledge_and_llm() -> None:
    routed = route_reply("请自我介绍，并说下最快多久到岗", _context())

    assert routed.source is ReplySource.RULE_TEMPLATE
    assert routed.result is not None
    assert routed.result.content == "我最快可以一周内到岗。"


def test_salary_question_uses_rule_reply() -> None:
    routed = route_reply("你的期望薪资范围是多少？", _context())

    assert routed.source is ReplySource.RULE_TEMPLATE
    assert routed.result is not None
    assert "远程岗位期望月薪25K" in routed.result.content


def test_location_question_uses_rule_reply() -> None:
    routed = route_reply("是否接受济南现场办公？", _context())

    assert routed.source is ReplySource.RULE_TEMPLATE
    assert routed.result is not None
    assert "只考虑济南" in routed.result.content


def test_work_mode_question_uses_rule_reply() -> None:
    routed = route_reply("是否接受远程办公？", _context())

    assert routed.source is ReplySource.RULE_TEMPLATE
    assert routed.result is not None
    assert "远程" in routed.result.content


def test_knowledge_reply_precedes_llm() -> None:
    routed = route_reply("请介绍一下你的项目经验", _context())

    assert routed.source is ReplySource.KNOWLEDGE_BASE
    assert routed.result is not None
    assert "OAuth2" in routed.result.content


def test_unmatched_message_routes_to_llm() -> None:
    routed = route_reply("你对我们团队有什么问题？", _context())

    assert routed.source is ReplySource.LLM
    assert routed.result is None
