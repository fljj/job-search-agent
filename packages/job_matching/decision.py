from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.job_matching.models import (
    ContactDecision,
    EffectiveJobStatus,
    HardRejectionReason,
    JobDecisionContext,
    JobDecisionResult,
)
from packages.llm.models import (
    JobContactDecisionOutput,
    JobContactDecisionRequest,
)

DECISION_VERSION = "llm-contact-decision:1.0.0"
MIN_CONTACT_CONFIDENCE = Decimal("0.75")


def build_llm_request(
    context: JobDecisionContext,
) -> JobContactDecisionRequest:
    """只发送判断是否值得沟通所需的职位、履历和策略摘要。"""

    parsed = context.parsed_job
    job = context.job
    candidate = context.candidate
    strategy = context.strategy
    corpus = " ".join(
        [
            job.title,
            *parsed.required_skills,
            *parsed.preferred_skills,
            *parsed.responsibilities,
        ]
    ).lower()
    relevant_skills = [
        {
            "name": skill.name,
            "years": skill.years,
            "core": skill.is_core,
        }
        for skill in candidate.skills
        if skill.is_core or skill.name.lower() in corpus
    ][:15]
    return JobContactDecisionRequest(
        job={
            "title": job.title,
            "industry": job.industry,
            "location": job.location,
            "work_mode": job.work_mode,
            "salary": job.salary_text,
            "recruiter_role": job.recruiter_role,
        },
        requirements={
            "required_skills": parsed.required_skills[:12],
            "preferred_skills": parsed.preferred_skills[:8],
            "years_required": parsed.years_required,
            "seniority": parsed.seniority_level,
            "management_required": parsed.management_required,
            "architecture_required": parsed.architecture_required,
            "responsibilities": parsed.responsibilities[:6],
        },
        candidate={
            "total_years": candidate.total_years,
            "management_years": candidate.management_years,
            "architecture_experience": candidate.has_architecture_experience,
            "core_system_experience": candidate.has_core_system_experience,
            "skills": relevant_skills,
            "industries": candidate.industry_experiences,
        },
        strategy={
            "target_titles": [
                rule.pattern
                for rule in strategy.title_rules
                if rule.rule_type.value == "INCLUDE"
            ],
            "work_modes": [
                {
                    "mode": rule.work_mode,
                    "locations": rule.allowed_locations,
                }
                for rule in strategy.work_mode_rules
                if rule.enabled
            ],
            "salary": [
                {
                    "mode": rule.work_mode,
                    "minimum_k": rule.minimum_monthly_k,
                    "expected_k": rule.expected_monthly_k,
                }
                for rule in strategy.salary_rules
            ],
            "industries": [
                {"name": rule.industry, "type": rule.rule_type}
                for rule in strategy.industry_rules
            ],
            "accept_part_time": strategy.accept_part_time,
            "accept_outsourcing": strategy.accept_outsourcing,
            "accept_headhunter": strategy.accept_headhunter,
        },
    )


def hard_filtered_result(
    context: JobDecisionContext,
    rejections: list[HardRejectionReason],
    *,
    now: datetime | None = None,
) -> JobDecisionResult:
    effective_status, blockers = effective_job_state(context, now=now)
    return JobDecisionResult(
        decision=ContactDecision.FILTERED_OUT,
        confidence=Decimal(1),
        hard_rejected=True,
        effective_job_status=effective_status,
        action_blockers=blockers,
        rejection_reasons=rejections,
        reason="职位命中硬性排除规则，未调用大模型。",
        automation_eligible=False,
        decision_version=DECISION_VERSION,
    )


def validate_llm_decision(
    context: JobDecisionContext,
    output: JobContactDecisionOutput,
    *,
    now: datetime | None = None,
) -> JobDecisionResult:
    effective_status, blockers = effective_job_state(context, now=now)
    eligible = (
        output.decision == ContactDecision.CONTACT
        and output.confidence >= MIN_CONTACT_CONFIDENCE
        and effective_status == EffectiveJobStatus.OPEN
        and not blockers
    )
    if (
        output.decision == ContactDecision.CONTACT
        and output.confidence < MIN_CONTACT_CONFIDENCE
    ):
        blockers.append("CONTACT_CONFIDENCE_BELOW_THRESHOLD")
    return JobDecisionResult(
        decision=ContactDecision(output.decision),
        confidence=output.confidence,
        hard_rejected=False,
        effective_job_status=effective_status,
        action_blockers=blockers,
        matched_evidence=output.matched_evidence,
        uncertainties=output.uncertainties,
        reason=output.reason,
        automation_eligible=eligible,
        decision_version=DECISION_VERSION,
    )


def effective_job_state(
    context: JobDecisionContext,
    *,
    now: datetime | None = None,
) -> tuple[EffectiveJobStatus, list[str]]:
    current = now or datetime.now(UTC)
    published_at = context.job.published_at
    if published_at is not None and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    if context.job.source_status.value == "CLOSED":
        return EffectiveJobStatus.CLOSED, ["JOB_CLOSED"]
    if (
        published_at
        and published_at < current - timedelta(days=context.strategy.max_posted_days)
    ):
        return EffectiveJobStatus.EXPIRED, ["JOB_EXPIRED"]
    if context.job.source_status.value == "OPEN":
        return EffectiveJobStatus.OPEN, []
    return EffectiveJobStatus.UNKNOWN, ["JOB_STATUS_UNKNOWN"]
