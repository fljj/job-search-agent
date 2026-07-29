from decimal import Decimal

import pytest

from packages.job_parser.models import SalaryRange, SeniorityLevel, WorkMode
from packages.scoring.hard_filters import evaluate_hard_filters
from packages.scoring.models import ScoringContext


def codes(context: ScoringContext) -> set[str]:
    return {item.rule_code for item in evaluate_hard_filters(context)}


def test_matching_job_has_no_rejection(context: ScoringContext) -> None:
    assert codes(context) == set()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"title": "Android开发工程师"}, "TITLE_EXCLUDED"),
        ({"work_mode": WorkMode.ONSITE, "location": "北京"}, "ONSITE_LOCATION_NOT_ALLOWED"),
        ({"work_mode": WorkMode.HYBRID, "location": "上海"}, "HYBRID_LOCATION_NOT_ALLOWED"),
        ({"industry": "教育培训"}, "INDUSTRY_EXCLUDED"),
        ({"company_name": "黑名单公司有限公司"}, "COMPANY_BLACKLISTED"),
    ],
)
def test_job_field_rejections(context: ScoringContext, change: dict[str, object], expected: str) -> None:
    changed = context.model_copy(update={"job": context.job.model_copy(update=change)})
    assert expected in codes(changed)


def test_disabled_work_mode(context: ScoringContext) -> None:
    rules = [rule.model_copy(update={"enabled": False}) if rule.work_mode == WorkMode.REMOTE else rule
             for rule in context.strategy.work_mode_rules]
    changed = context.model_copy(update={"strategy": context.strategy.model_copy(update={"work_mode_rules": rules})})
    assert "WORK_MODE_DISABLED" in codes(changed)


def test_missing_core_skill(context: ScoringContext) -> None:
    candidate = context.candidate.model_copy(update={"skills": []})
    assert "REQUIRED_CORE_SKILL_MISSING" in codes(context.model_copy(update={"candidate": candidate}))


def test_salary_below_minimum(context: ScoringContext) -> None:
    parsed = context.parsed_job.model_copy(update={"salary": SalaryRange(
        minimum_monthly_k=Decimal(20), maximum_monthly_k=Decimal(30), is_pre_tax=True)})
    assert "SALARY_BELOW_MINIMUM" in codes(context.model_copy(update={"parsed_job": parsed}))


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"outsourcing_detected": True}, "OUTSOURCING_NOT_ACCEPTED"),
        ({"headhunter_detected": True}, "HEADHUNTER_NOT_ACCEPTED"),
        ({"internship_detected": True}, "INTERNSHIP_POSITION"),
        ({"seniority_level": SeniorityLevel.JUNIOR}, "JUNIOR_POSITION"),
    ],
)
def test_parsed_rejections(context: ScoringContext, changes: dict[str, object], expected: str) -> None:
    parsed = context.parsed_job.model_copy(update=changes)
    assert expected in codes(context.model_copy(update={"parsed_job": parsed}))


def test_unknown_salary_does_not_hard_reject(context: ScoringContext) -> None:
    parsed = context.parsed_job.model_copy(update={"salary": None})
    assert "SALARY_BELOW_MINIMUM" not in codes(context.model_copy(update={"parsed_job": parsed}))


def test_part_time_is_rejected_when_strategy_does_not_accept_it(
    context: ScoringContext,
) -> None:
    parsed = context.parsed_job.model_copy(update={"part_time_detected": True})

    assert "PART_TIME_NOT_ACCEPTED" in codes(
        context.model_copy(update={"parsed_job": parsed})
    )


def test_accepted_part_time_without_explicit_onsite_is_not_rejected_by_location(
    context: ScoringContext,
) -> None:
    job = context.job.model_copy(
        update={"work_mode": WorkMode.ONSITE, "location": "杭州"}
    )
    parsed = context.parsed_job.model_copy(
        update={
            "part_time_detected": True,
            "onsite_required_explicitly": False,
        }
    )
    strategy = context.strategy.model_copy(update={"accept_part_time": True})

    result = codes(
        context.model_copy(
            update={"job": job, "parsed_job": parsed, "strategy": strategy}
        )
    )
    assert "PART_TIME_NOT_ACCEPTED" not in result
    assert "ONSITE_LOCATION_NOT_ALLOWED" not in result


def test_accepted_part_time_with_explicit_onsite_still_obeys_location_policy(
    context: ScoringContext,
) -> None:
    job = context.job.model_copy(
        update={"work_mode": WorkMode.ONSITE, "location": "杭州"}
    )
    parsed = context.parsed_job.model_copy(
        update={
            "part_time_detected": True,
            "onsite_required_explicitly": True,
        }
    )
    strategy = context.strategy.model_copy(update={"accept_part_time": True})

    assert "ONSITE_LOCATION_NOT_ALLOWED" in codes(
        context.model_copy(
            update={"job": job, "parsed_job": parsed, "strategy": strategy}
        )
    )


def test_explicit_full_time_bachelor_requirement_rejects_non_full_time_candidate(
    context: ScoringContext,
) -> None:
    candidate = context.candidate.model_copy(
        update={"bachelor_full_time": False}
    )
    strategy = context.strategy.model_copy(
        update={"reject_full_time_bachelor_required": True}
    )
    parsed = context.parsed_job.model_copy(
        update={"full_time_bachelor_required": True}
    )

    assert "FULL_TIME_BACHELOR_REQUIRED" in codes(
        context.model_copy(
            update={
                "candidate": candidate,
                "strategy": strategy,
                "parsed_job": parsed,
            }
        )
    )


@pytest.mark.parametrize(
    ("candidate_value", "strategy_enabled", "explicit_requirement"),
    [
        (False, True, False),
        (False, False, True),
        (None, True, True),
        (True, True, True),
    ],
)
def test_full_time_bachelor_rule_does_not_guess_or_override_strategy(
    context: ScoringContext,
    candidate_value: bool | None,
    strategy_enabled: bool,
    explicit_requirement: bool,
) -> None:
    changed = context.model_copy(
        update={
            "candidate": context.candidate.model_copy(
                update={"bachelor_full_time": candidate_value}
            ),
            "strategy": context.strategy.model_copy(
                update={
                    "reject_full_time_bachelor_required": strategy_enabled
                }
            ),
            "parsed_job": context.parsed_job.model_copy(
                update={
                    "full_time_bachelor_required": explicit_requirement
                }
            ),
        }
    )

    assert "FULL_TIME_BACHELOR_REQUIRED" not in codes(changed)
