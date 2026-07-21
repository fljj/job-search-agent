from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from packages.job_parser.models import SourceJobStatus
from packages.scoring.engine import grade_for_score, score_job
from packages.scoring.models import EffectiveJobStatus, Eligibility, Grade, ScoringContext


def test_engine_sums_dimensions_and_assigns_grade(context: ScoringContext) -> None:
    result = score_job(context)
    expected = int(sum(result.dimension_scores.values()).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    assert result.total_score == expected
    assert result.grade == Grade.A
    assert result.eligibility == Eligibility.ELIGIBLE


def test_closed_job_is_action_blocker_not_rejection(context: ScoringContext) -> None:
    job = context.job.model_copy(update={"source_status": SourceJobStatus.CLOSED})
    result = score_job(context.model_copy(update={"job": job}))
    assert result.effective_job_status == EffectiveJobStatus.CLOSED
    assert result.action_blockers == ["JOB_CLOSED"]
    assert result.eligibility == Eligibility.ELIGIBLE


def test_old_job_is_action_blocker(context: ScoringContext) -> None:
    job = context.job.model_copy(update={"published_at": datetime.now(UTC) - timedelta(days=31)})
    result = score_job(context.model_copy(update={"job": job}))
    assert result.effective_job_status == EffectiveJobStatus.EXPIRED
    assert result.action_blockers == ["JOB_TOO_OLD"]


@pytest.mark.parametrize(("score", "grade"), [(59, Grade.C), (60, Grade.B), (69, Grade.B),
                                                (70, Grade.A), (100, Grade.A)])
def test_grade_boundaries(score: int, grade: Grade) -> None:
    assert grade_for_score(score) == grade
