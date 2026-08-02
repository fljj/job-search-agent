from dataclasses import dataclass, field

from packages.conversation_agent.knowledge.profile_answer import (
    CandidateKnowledge,
    build_profile_answer,
)
from packages.conversation_agent.models import DraftResult, ReplySource
from packages.conversation_agent.rules.arrival import build_arrival_reply
from packages.conversation_agent.rules.location import build_location_reply
from packages.conversation_agent.rules.salary import SalaryExpectation, build_salary_reply
from packages.conversation_agent.rules.work_mode import build_work_mode_reply


@dataclass(frozen=True)
class ReplyRouteContext:
    arrival_time_reply: str | None = None
    salary_expectations: list[SalaryExpectation] = field(default_factory=list)
    onsite_locations: list[str] = field(default_factory=list)
    enabled_work_modes: list[str] = field(default_factory=list)
    candidate_knowledge: CandidateKnowledge | None = None


@dataclass(frozen=True)
class RoutedReply:
    source: ReplySource
    result: DraftResult | None


def route_reply(content: str, context: ReplyRouteContext) -> RoutedReply:
    """按 Rule > Knowledge > LLM 的固定优先级选择回复来源。"""
    rule_results = (
        build_arrival_reply(content, context.arrival_time_reply),
        build_salary_reply(content, context.salary_expectations),
        build_location_reply(content, context.onsite_locations),
        build_work_mode_reply(content, context.enabled_work_modes),
    )
    for result in rule_results:
        if result is not None:
            return RoutedReply(ReplySource.RULE_TEMPLATE, result)
    # 知识库只提供可信事实，不直接拼接对招聘方的自然语言回复。
    # 命中知识问题后仍交给 LLM 结合当前消息和对话历史组织表达。
    if build_profile_answer(content, context.candidate_knowledge) is not None:
        return RoutedReply(ReplySource.LLM, None)
    return RoutedReply(ReplySource.LLM, None)
