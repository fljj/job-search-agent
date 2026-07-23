from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from packages.job_parser.models import SourceJobStatus
from packages.llm.models import JobScoreOutput
from packages.scoring.engine import grade_for_score
from packages.scoring.hard_filters import evaluate_hard_filters
from packages.scoring.models import (
    DIMENSION_MAX,
    EffectiveJobStatus,
    Eligibility,
    ScoreDetail,
    ScoreResult,
    ScoringContext,
)

LLM_SCORING_VERSION = "llm:1.0.0"

EVIDENCE_REFS: dict[str, set[str]] = {
    "title": {"job.title", "strategy.title_rules"},
    "skills": {
        "parsed_job.required_skills",
        "parsed_job.preferred_skills",
        "candidate.skills",
        "strategy.core_required_skills",
    },
    "experience": {
        "parsed_job.years_required",
        "candidate.total_years",
        "candidate.has_core_system_experience",
        "candidate.industry_experiences",
    },
    "location": {"job.work_mode", "job.location", "strategy.work_mode_rules"},
    "salary": {"job.salary_text", "parsed_job.salary", "strategy.salary_rules"},
    "industry": {
        "job.industry",
        "candidate.industry_experiences",
        "strategy.industry_rules",
    },
    "management": {
        "parsed_job.management_required",
        "parsed_job.seniority_level",
        "candidate.management_years",
        "candidate.has_architecture_experience",
    },
}


class LlmScoreValidationError(ValueError):
    pass


def validate_llm_score(
    context: ScoringContext,
    output: JobScoreOutput,
    *,
    now: datetime | None = None,
) -> ScoreResult:
    """校验模型评分，并由程序形成最终分数、等级、硬排除和动作约束。"""
    dimensions = [item.dimension for item in output.dimensions]
    expected = set(DIMENSION_MAX)
    if len(dimensions) != len(set(dimensions)):
        raise LlmScoreValidationError("模型评分包含重复维度")
    if set(dimensions) != expected:
        raise LlmScoreValidationError("模型评分维度不完整或包含未知维度")

    details: list[ScoreDetail] = []
    for item in output.dimensions:
        expected_max = DIMENSION_MAX[item.dimension]
        if item.max_score != expected_max:
            raise LlmScoreValidationError(f"{item.dimension} 维度满分不符合契约")
        if item.score > expected_max:
            raise LlmScoreValidationError(f"{item.dimension} 维度分数越界")
        exponent = item.score.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -2:
            raise LlmScoreValidationError(f"{item.dimension} 维度分数最多保留两位小数")
        if not item.evidence_refs:
            raise LlmScoreValidationError(f"{item.dimension} 维度缺少证据引用")
        invalid_refs = set(item.evidence_refs) - EVIDENCE_REFS[item.dimension]
        if invalid_refs:
            raise LlmScoreValidationError(f"{item.dimension} 维度包含非法证据引用")
        details.append(
            ScoreDetail(
                dimension=item.dimension,
                score=item.score,
                max_score=item.max_score,
                rule_code="LLM_SEMANTIC_SCORE",
                explanation=item.reason,
                evidence_refs=item.evidence_refs,
                matched_facts={"evidence_refs": item.evidence_refs},
            )
        )

    total_decimal = sum((item.score for item in output.dimensions), start=Decimal(0))
    total = int(total_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if output.total_score != total:
        raise LlmScoreValidationError("模型总分与七个维度之和不一致")

    rejections = evaluate_hard_filters(context)
    effective_status = _effective_status(context, now or datetime.now(UTC))
    blockers: list[str] = []
    if effective_status == EffectiveJobStatus.CLOSED:
        blockers.append("JOB_CLOSED")
    elif effective_status == EffectiveJobStatus.EXPIRED:
        blockers.append("JOB_TOO_OLD")

    return ScoreResult(
        total_score=total,
        grade=grade_for_score(total),
        eligibility=Eligibility.FILTERED_OUT if rejections else Eligibility.ELIGIBLE,
        hard_rejected=bool(rejections),
        effective_job_status=effective_status,
        action_blockers=blockers,
        dimension_scores={item.dimension: item.score for item in output.dimensions},
        details=details,
        rejection_reasons=rejections,
        match_reasons=output.match_reasons,
        risk_notes=[*output.risk_notes, *context.parsed_job.warnings],
        scoring_version=LLM_SCORING_VERSION,
    )


def is_automation_eligible(result: ScoreResult, recommends_proactive_contact: bool) -> bool:
    return (
        not result.hard_rejected
        and result.effective_job_status == EffectiveJobStatus.OPEN
        and result.total_score >= 80
        and recommends_proactive_contact
    )


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
