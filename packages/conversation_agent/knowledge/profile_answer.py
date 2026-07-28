from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from packages.conversation_agent.models import Decision, DraftResult, Intent
from packages.knowledge_base.models import KnowledgeFact


@dataclass(frozen=True)
class CandidateKnowledge:
    name: str
    total_years: Decimal
    management_years: Decimal
    profile_id: UUID
    skills: list[tuple[UUID, str, Decimal | None]] = field(default_factory=list)
    facts: list[KnowledgeFact] = field(default_factory=list)


def build_profile_answer(content: str, knowledge: CandidateKnowledge | None) -> DraftResult | None:
    if knowledge is None:
        return None
    if any(term in content for term in ("自我介绍", "介绍一下自己", "简单介绍下自己")):
        skill_names = "、".join(item[1] for item in knowledge.skills[:6])
        suffix = f"，主要技术栈包括{skill_names}" if skill_names else ""
        return _result(
            f"您好，我是{knowledge.name}，有{knowledge.total_years:g}年工作经验{suffix}。",
            [Intent.WORK_EXPERIENCE],
            [knowledge.profile_id, *(item[0] for item in knowledge.skills[:6])],
            "KNOWLEDGE_PROFILE_INTRODUCTION",
        )
    if any(term in content for term in ("项目经验", "做过什么项目", "项目介绍")):
        facts = _matching_facts(knowledge.facts, ("PROJECT", "PROJECT_EXPERIENCE", "项目"))
        if facts:
            return _facts_result(facts, Intent.PROJECT_EXPERIENCE, "KNOWLEDGE_PROJECT_REPLY")
    if any(term in content.lower() for term in ("技术栈", "技术经验", "擅长技术", "java经验")):
        if knowledge.skills:
            descriptions = [
                f"{name}" + (f"（{years:g}年）" if years is not None else "")
                for _, name, years in knowledge.skills[:8]
            ]
            return _result(
                f"我的主要技术栈包括{'、'.join(descriptions)}。",
                [Intent.TECH_STACK],
                [item[0] for item in knowledge.skills[:8]],
                "KNOWLEDGE_SKILL_REPLY",
            )
    if any(term in content for term in ("管理经验", "带过团队", "团队管理", "带人经验")):
        if knowledge.management_years > 0:
            return _result(
                f"我有{knowledge.management_years:g}年团队管理经验，具体团队规模和职责可以继续沟通。",
                [Intent.MANAGEMENT_EXPERIENCE],
                [knowledge.profile_id],
                "KNOWLEDGE_MANAGEMENT_REPLY",
            )
    return None


def _matching_facts(facts: list[KnowledgeFact], categories: tuple[str, ...]) -> list[KnowledgeFact]:
    return [
        fact for fact in facts
        if fact.allowed_for_auto_reply
        and fact.sensitivity.value == "NORMAL"
        and any(term in fact.category.upper() for term in categories)
    ][:3]


def _facts_result(
    facts: list[KnowledgeFact], intent: Intent, reason_code: str
) -> DraftResult:
    return _result(
        "；".join(fact.fact.rstrip("。；") for fact in facts) + "。",
        [intent],
        [fact.id for fact in facts if fact.id],
        reason_code,
    )


def _result(
    content: str, intents: list[Intent], fact_ids: list[UUID], reason_code: str
) -> DraftResult:
    return DraftResult(
        content=content,
        intents=intents,
        fact_ids=fact_ids,
        confidence=1,
        decision=Decision.ALLOW_AUTO,
        reason_codes=[reason_code],
    )
