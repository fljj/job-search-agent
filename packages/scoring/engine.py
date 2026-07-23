from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from packages.job_parser.models import SourceJobStatus
from packages.scoring.dimensions.experience import score_experience
from packages.scoring.dimensions.industry import score_industry
from packages.scoring.dimensions.location import score_location
from packages.scoring.dimensions.management import score_management
from packages.scoring.dimensions.salary import score_salary
from packages.scoring.dimensions.skills import score_skills
from packages.scoring.dimensions.title import score_title
from packages.scoring.hard_filters import evaluate_hard_filters
from packages.scoring.models import (
    EffectiveJobStatus,
    Eligibility,
    Grade,
    ScoreResult,
    ScoringContext,
)
from packages.scoring.reasons import build_match_reasons, build_risk_notes

SCORING_VERSION = "legacy:1.0.0"


def score_job(context: ScoringContext, *, now: datetime | None = None) -> ScoreResult:
    details = [
        score_title(context),
        score_skills(context),
        score_experience(context),
        score_location(context),
        score_salary(context),
        score_industry(context),
        score_management(context),
    ]
    rejections = evaluate_hard_filters(context)
    total_decimal = sum((detail.score for detail in details), start=Decimal(0))
    total = int(total_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    grade = grade_for_score(total)
    effective_status = _effective_status(context, now or datetime.now(UTC))
    blockers: list[str] = []
    if effective_status == EffectiveJobStatus.CLOSED:
        blockers.append("JOB_CLOSED")
    elif effective_status == EffectiveJobStatus.EXPIRED:
        blockers.append("JOB_TOO_OLD")
    return ScoreResult(
        total_score=total,
        grade=grade,
        eligibility=Eligibility.FILTERED_OUT if rejections else Eligibility.ELIGIBLE,
        hard_rejected=bool(rejections),
        effective_job_status=effective_status,
        action_blockers=blockers,
        dimension_scores={detail.dimension: detail.score for detail in details},
        details=details,
        rejection_reasons=rejections,
        match_reasons=build_match_reasons(details),
        risk_notes=build_risk_notes(details, rejections, context.parsed_job.warnings),
        scoring_version=SCORING_VERSION,
    )


def grade_for_score(total: int) -> Grade:
    if not 0 <= total <= 100:
        raise ValueError("总分必须在 0 到 100 之间")
    return Grade.A if total >= 70 else Grade.B if total >= 60 else Grade.C


def _effective_status(context: ScoringContext, now: datetime) -> EffectiveJobStatus:
    if context.job.source_status == SourceJobStatus.CLOSED:
        return EffectiveJobStatus.CLOSED
    if context.job.published_at:
        published = context.job.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        if (now - published).days > context.strategy.max_posted_days:
            return EffectiveJobStatus.EXPIRED
    if context.job.source_status == SourceJobStatus.UNKNOWN:
        return EffectiveJobStatus.UNKNOWN
    return EffectiveJobStatus.OPEN
