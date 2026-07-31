import re
from dataclasses import dataclass
from decimal import Decimal

from packages.conversation_agent.models import Decision, DraftResult, Intent

SALARY_TERMS = ("期望薪资", "薪资要求", "薪资范围")
SALARY_TOPIC_TERMS = ("薪资", "薪水", "工资", "待遇")
EXPECTATION_TERMS = ("期望", "要求", "预期")
QUESTION_TERMS = ("多少", "什么范围", "怎么考虑")
MODE_LABELS = {"REMOTE": "远程", "ONSITE": "现场", "HYBRID": "混合办公"}


@dataclass(frozen=True)
class SalaryExpectation:
    work_mode: str
    currency: str
    expected_monthly_k: Decimal


def build_salary_reply(
    content: str, expectations: list[SalaryExpectation]
) -> DraftResult | None:
    explicit_question = any(term in content for term in SALARY_TERMS)
    conversational_question = (
        any(term in content for term in SALARY_TOPIC_TERMS)
        and any(term in content for term in EXPECTATION_TERMS)
        and any(term in content for term in QUESTION_TERMS)
    )
    contextual_question = bool(
        re.search(
            r"(?:您|你)?(?:目前|现在)?(?:的)?期望(?:的)?(?:是|大概是|在)?多少",
            content,
        )
    ) and not any(term in content for term in ("岗位期望", "工作期望", "职业期望"))
    if not (explicit_question or conversational_question or contextual_question) or not expectations:
        return None
    parts = [
        f"{MODE_LABELS.get(item.work_mode, item.work_mode)}岗位期望月薪"
        f"{item.expected_monthly_k.normalize():f}K"
        for item in expectations
        if item.currency == "CNY"
    ]
    if not parts:
        return None
    return DraftResult(
        content=f"我的薪资期望是：{'；'.join(parts)}。具体可以结合岗位职责和整体方案沟通。",
        intents=[Intent.SALARY],
        confidence=1,
        decision=Decision.ALLOW_AUTO,
        reason_codes=["CONFIGURED_SALARY_EXPECTATION_REPLY"],
    )
