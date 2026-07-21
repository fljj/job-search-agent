from decimal import Decimal

from packages.job_parser.models import ParsedJob, SalaryRange, WorkMode
from packages.scoring.dimensions.experience import score_experience
from packages.scoring.dimensions.industry import score_industry
from packages.scoring.dimensions.location import score_location
from packages.scoring.dimensions.management import score_management
from packages.scoring.dimensions.salary import score_salary
from packages.scoring.dimensions.skills import score_skills
from packages.scoring.dimensions.title import score_title
from packages.scoring.models import ScoringContext


def test_title_contains_target_direction(context: ScoringContext) -> None:
    assert score_title(context).score == 12


def test_skills_all_match(context: ScoringContext) -> None:
    assert score_skills(context).score == 25


def test_only_preferred_skills_are_capped(context: ScoringContext) -> None:
    changed = context.model_copy(update={"parsed_job": ParsedJob(preferred_skills=["Java"])})
    assert score_skills(changed).score == Decimal("12.50")


def test_experience_full_match(context: ScoringContext) -> None:
    assert score_experience(context).score == 15


def test_remote_location_ignores_city(context: ScoringContext) -> None:
    assert score_location(context).score == 15


def test_onsite_jinan_full_score(context: ScoringContext) -> None:
    changed_job = context.job.model_copy(update={"work_mode": WorkMode.ONSITE, "location": "济南"})
    assert score_location(context.model_copy(update={"job": changed_job})).score == 15


def test_unknown_work_mode_scores_eight(context: ScoringContext) -> None:
    changed_job = context.job.model_copy(update={"work_mode": WorkMode.UNKNOWN})
    assert score_location(context.model_copy(update={"job": changed_job})).score == 8


def test_remote_salary_40k_is_full_score(context: ScoringContext) -> None:
    assert score_salary(context).score == 15


def test_salary_negotiable_scores_configured_value(context: ScoringContext) -> None:
    changed = context.parsed_job.model_copy(update={"salary": SalaryRange(negotiable=True)})
    assert score_salary(context.model_copy(update={"parsed_job": changed})).score == 8


def test_preferred_industry_full_score(context: ScoringContext) -> None:
    assert score_industry(context).score == 10


def test_unknown_industry_scores_four(context: ScoringContext) -> None:
    changed_job = context.job.model_copy(update={"industry": "制造业"})
    assert score_industry(context.model_copy(update={"job": changed_job})).score == 4


def test_management_without_requirement_gets_neutral_score(context: ScoringContext) -> None:
    assert score_management(context).score == Decimal("2.5")


def test_management_and_architecture_full_score(context: ScoringContext) -> None:
    changed = context.parsed_job.model_copy(update={"management_required": True,
                                                    "architecture_required": True})
    assert score_management(context.model_copy(update={"parsed_job": changed})).score == 5
