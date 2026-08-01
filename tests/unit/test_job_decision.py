from decimal import Decimal

from packages.job_matching.decision import (
    build_llm_request,
    hard_filtered_result,
    validate_llm_decision,
)
from packages.job_matching.models import ContactDecision
from packages.llm.models import JobContactDecisionOutput


def test_compact_request_contains_relevant_candidate_facts_only(context) -> None:
    request = build_llm_request(context)

    skills = request.candidate["skills"]
    names = [item["name"] for item in skills]
    assert "Java" in names
    assert len(names) <= 15
    assert "evidence_catalog" not in request.model_dump()


def test_contact_decision_is_eligible_when_confident_and_open(context) -> None:
    result = validate_llm_decision(
        context,
        JobContactDecisionOutput(
            decision="CONTACT",
            confidence=Decimal("0.90"),
            matched_evidence=["Java经验满足"],
            uncertainties=[],
            reason="核心方向和技能匹配",
        ),
    )

    assert result.decision is ContactDecision.CONTACT
    assert result.automation_eligible is True


def test_low_confidence_contact_is_not_eligible(context) -> None:
    result = validate_llm_decision(
        context,
        JobContactDecisionOutput(
            decision="CONTACT",
            confidence=Decimal("0.70"),
            matched_evidence=[],
            uncertainties=["职责信息不足"],
            reason="需要更多信息",
        ),
    )

    assert result.automation_eligible is False
    assert "CONTACT_CONFIDENCE_BELOW_THRESHOLD" in result.action_blockers


def test_hard_filter_result_never_allows_contact(context) -> None:
    from packages.job_matching.models import HardRejectionReason

    result = hard_filtered_result(
        context,
        [HardRejectionReason(rule_code="COMPANY_BLACKLIST", message="公司黑名单")],
    )

    assert result.decision is ContactDecision.FILTERED_OUT
    assert result.hard_rejected is True
    assert result.automation_eligible is False
