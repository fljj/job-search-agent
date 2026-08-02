from decimal import Decimal

import pytest

from packages.job_matching.hard_filters import evaluate_hard_filters
from packages.job_matching.models import JobDecisionContext
from packages.job_parser.models import SalaryRange, SeniorityLevel, WorkMode


def codes(context: JobDecisionContext) -> set[str]:
    return {item.rule_code for item in evaluate_hard_filters(context)}


def test_matching_job_has_no_rejection(context: JobDecisionContext) -> None:
    assert codes(context) == set()


@pytest.mark.parametrize(
    ("title", "description"),
    [
        (
            "数控机床维修工程师",
            "熟悉机床维修、贴塑刮研和机床电路，能排除设备故障。",
        ),
        (
            "中医减肥（本院直招）",
            "日常坐诊，完成问诊、脉诊、针灸和诊疗工作。",
        ),
    ],
)
def test_unrelated_full_job_is_rejected_before_llm(
    context: JobDecisionContext,
    title: str,
    description: str,
) -> None:
    changed = context.model_copy(
        update={
            "job": context.job.model_copy(
                update={"title": title, "description": description}
            ),
            "parsed_job": context.parsed_job.model_copy(
                update={
                    "required_skills": [],
                    "preferred_skills": [],
                    "responsibilities": [description],
                }
            ),
        }
    )

    result = evaluate_hard_filters(
        changed,
        direction_keywords=["Java", "后端", "服务端", "开发", "研发", "AI", "直播运营"],
    )

    assert {item.rule_code for item in result} == {"JOB_DIRECTION_CONFLICT"}


def test_nonstandard_title_with_relevant_jd_is_not_direction_rejected(
    context: JobDecisionContext,
) -> None:
    job = context.job.model_copy(
        update={
            "title": "银行系统工程师",
            "description": "负责银行核心系统服务端研发和微服务架构。",
        }
    )
    changed = context.model_copy(update={"job": job})

    result = evaluate_hard_filters(
        changed,
        direction_keywords=["Java", "后端", "服务端", "开发", "研发"],
    )

    assert "JOB_DIRECTION_CONFLICT" not in {
        item.rule_code for item in result
    }


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
def test_job_field_rejections(context: JobDecisionContext, change: dict[str, object], expected: str) -> None:
    changed = context.model_copy(update={"job": context.job.model_copy(update=change)})
    assert expected in codes(changed)


@pytest.mark.parametrize(
    "location",
    ["济南-高新区", "济南市历下区", "山东济南高新区", "山东省济南市高新区"],
)
def test_onsite_city_allows_subordinate_districts(
    context: JobDecisionContext,
    location: str,
) -> None:
    changed = context.model_copy(
        update={
            "job": context.job.model_copy(
                update={"work_mode": WorkMode.ONSITE, "location": location}
            )
        }
    )

    assert "ONSITE_LOCATION_NOT_ALLOWED" not in codes(changed)


def test_disabled_work_mode(context: JobDecisionContext) -> None:
    rules = [rule.model_copy(update={"enabled": False}) if rule.work_mode == WorkMode.REMOTE else rule
             for rule in context.strategy.work_mode_rules]
    changed = context.model_copy(update={"strategy": context.strategy.model_copy(update={"work_mode_rules": rules})})
    assert "WORK_MODE_DISABLED" in codes(changed)


def test_missing_core_skill(context: JobDecisionContext) -> None:
    candidate = context.candidate.model_copy(update={"skills": []})
    assert "REQUIRED_CORE_SKILL_MISSING" in codes(context.model_copy(update={"candidate": candidate}))


def test_salary_below_contact_threshold(context: JobDecisionContext) -> None:
    parsed = context.parsed_job.model_copy(update={"salary": SalaryRange(
        minimum_monthly_k=Decimal(20), maximum_monthly_k=Decimal(30), is_pre_tax=True)})
    assert "SALARY_BELOW_CONTACT_THRESHOLD" in codes(
        context.model_copy(update={"parsed_job": parsed})
    )


@pytest.mark.parametrize(
    ("work_mode", "maximum", "rejected"),
    [
        (WorkMode.ONSITE, Decimal("14"), True),
        (WorkMode.ONSITE, Decimal("15"), False),
        (WorkMode.REMOTE, Decimal("24"), True),
        (WorkMode.REMOTE, Decimal("25"), False),
        (WorkMode.UNKNOWN, Decimal("10"), True),
    ],
)
def test_salary_contact_threshold_boundaries(
    context: JobDecisionContext,
    work_mode: WorkMode,
    maximum: Decimal,
    rejected: bool,
) -> None:
    salary_rules = [
        rule.model_copy(
            update={
                "expected_monthly_k": (
                    Decimal("25")
                    if rule.work_mode == WorkMode.REMOTE
                    else Decimal("15")
                )
            }
        )
        for rule in context.strategy.salary_rules
    ]
    changed = context.model_copy(
        update={
            "job": context.job.model_copy(
                update={"work_mode": work_mode, "location": "济南"}
            ),
            "parsed_job": context.parsed_job.model_copy(
                update={
                    "salary": SalaryRange(
                        minimum_monthly_k=maximum,
                        maximum_monthly_k=maximum,
                        is_pre_tax=True,
                    )
                }
            ),
            "strategy": context.strategy.model_copy(
                update={"salary_rules": salary_rules}
            ),
        }
    )

    assert (
        "SALARY_BELOW_CONTACT_THRESHOLD" in codes(changed)
    ) is rejected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"outsourcing_detected": True}, "OUTSOURCING_NOT_ACCEPTED"),
        ({"headhunter_detected": True}, "HEADHUNTER_NOT_ACCEPTED"),
        ({"internship_detected": True}, "INTERNSHIP_POSITION"),
        ({"seniority_level": SeniorityLevel.JUNIOR}, "JUNIOR_POSITION"),
    ],
)
def test_parsed_rejections(context: JobDecisionContext, changes: dict[str, object], expected: str) -> None:
    parsed = context.parsed_job.model_copy(update=changes)
    assert expected in codes(context.model_copy(update={"parsed_job": parsed}))


def test_unknown_salary_does_not_hard_reject(context: JobDecisionContext) -> None:
    parsed = context.parsed_job.model_copy(update={"salary": None})
    assert "SALARY_BELOW_CONTACT_THRESHOLD" not in codes(
        context.model_copy(update={"parsed_job": parsed})
    )


def test_part_time_is_rejected_when_strategy_does_not_accept_it(
    context: JobDecisionContext,
) -> None:
    parsed = context.parsed_job.model_copy(update={"part_time_detected": True})

    assert "PART_TIME_NOT_ACCEPTED" in codes(
        context.model_copy(update={"parsed_job": parsed})
    )


def test_accepted_part_time_without_explicit_onsite_is_not_rejected_by_location(
    context: JobDecisionContext,
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
    context: JobDecisionContext,
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
    context: JobDecisionContext,
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


def test_raw_jd_full_time_bachelor_requirement_overrides_parser_miss(
    context: JobDecisionContext,
) -> None:
    changed = context.model_copy(
        update={
            "candidate": context.candidate.model_copy(
                update={"bachelor_full_time": False}
            ),
            "strategy": context.strategy.model_copy(
                update={"reject_full_time_bachelor_required": True}
            ),
            "job": context.job.model_copy(
                update={
                    "description": "职位要求：全日制一类本科以上学历，计算机相关专业。"
                }
            ),
            "parsed_job": context.parsed_job.model_copy(
                update={"full_time_bachelor_required": False}
            ),
        }
    )

    assert "FULL_TIME_BACHELOR_REQUIRED" in codes(changed)


@pytest.mark.parametrize(
    "description",
    ["本科不限是否全日制", "不要求全日制本科", "全日制本科优先"],
)
def test_non_mandatory_full_time_bachelor_wording_is_not_hard_rejected(
    context: JobDecisionContext,
    description: str,
) -> None:
    changed = context.model_copy(
        update={
            "candidate": context.candidate.model_copy(
                update={"bachelor_full_time": False}
            ),
            "strategy": context.strategy.model_copy(
                update={"reject_full_time_bachelor_required": True}
            ),
            "job": context.job.model_copy(update={"description": description}),
            "parsed_job": context.parsed_job.model_copy(
                update={"full_time_bachelor_required": False}
            ),
        }
    )

    assert "FULL_TIME_BACHELOR_REQUIRED" not in codes(changed)


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
    context: JobDecisionContext,
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
