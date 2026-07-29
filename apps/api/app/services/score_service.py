import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adapters.llm.errors import LlmProviderError
from apps.api.app.core.config import get_settings
from apps.api.app.core.llm import build_llm_provider
from apps.api.app.models import entities as db
from apps.api.app.schemas.score import ScoreRequest, ScoreResponse
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.job_service import (
    get_job_entity,
    get_parsed_entity,
    parse_job,
    to_job_domain,
    to_parsed_domain,
)
from apps.api.app.services.llm_service import record_llm_invocation
from apps.api.app.services.strategy_service import to_domain as strategy_to_domain
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.job_parser.models import WorkMode
from packages.llm.models import LlmCallMetadata
from packages.llm.ports import LlmProvider
from packages.scoring.engine import score_job as score_job_deterministically
from packages.scoring.evidence import with_evidence_catalog
from packages.scoring.hard_filters import evaluate_hard_filters
from packages.scoring.llm_engine import (
    LLM_SCORING_VERSION,
    LlmScoreValidationError,
    is_automation_eligible,
    validate_llm_score,
)
from packages.scoring.models import (
    DIMENSION_MAX,
    CandidateProfile,
    CandidateSkill,
    RejectionReason,
    ScoringContext,
)

HARD_FILTER_SCORING_VERSION = "hard-filter:1.0.0"
HARD_FILTER_PROMPT_VERSION = "not-applicable"


def create_score(
    session: Session,
    job_id: object,
    request: ScoreRequest,
    provider: LlmProvider | None = None,
    reassessment_key: str | None = None,
) -> ScoreResponse:
    job = get_job_entity(session, job_id)
    strategy = session.get(db.JobStrategy, request.strategy_id)
    profile = session.get(db.CandidateProfile, request.candidate_profile_id)
    if strategy is None or strategy.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("策略不存在")
    if profile is None or profile.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("候选人资料不存在")
    if not strategy.enabled:
        raise ValueError("已停用策略不能用于新评分")
    if strategy.candidate_profile_id != profile.id:
        raise ValueError("策略与候选人资料不匹配")
    candidate = CandidateProfile(
        name=profile.name, total_years=profile.total_years,
        management_years=profile.management_years,
        has_architecture_experience=profile.has_architecture_experience,
        has_core_system_experience=profile.has_core_system_experience,
        bachelor_full_time=profile.bachelor_full_time,
        skills=[CandidateSkill(name=item.name, years=item.years, source=item.source,
                               is_core=item.is_core) for item in profile.skills],
        industry_experiences=[item.industry_code for item in profile.industries],
        version=profile.version,
    )
    parsed_record: db.ParsedJobDetail | None
    if request.parsed_job_detail_id:
        parsed_record = get_parsed_entity(session, request.parsed_job_detail_id)
        if parsed_record.job_id != job.id:
            raise ValueError("解析记录不属于当前职位")
    else:
        parsed_record = session.scalar(
            select(db.ParsedJobDetail)
            .where(
                db.ParsedJobDetail.job_id == job.id,
                db.ParsedJobDetail.parser_type == "RULE",
            )
            .order_by(
                db.ParsedJobDetail.created_at.desc(),
                db.ParsedJobDetail.id.desc(),
            )
            .limit(1)
        )
        if parsed_record is None:
            parsed_response = parse_job(
                session, job.id, "RULE"
            )
            parsed_record = get_parsed_entity(session, parsed_response.id)
    assert parsed_record is not None
    context = _scoring_context(job, parsed_record, candidate, strategy)
    rejections = evaluate_hard_filters(context)
    if rejections:
        return _persist_hard_filtered_score(
            session,
            job,
            strategy,
            profile,
            parsed_record,
            context,
            rejections,
            reassessment_key,
        )

    llm_provider = provider or build_llm_provider(get_settings())
    if request.parsed_job_detail_id is None:
        llm_parsed_record = session.scalar(
            select(db.ParsedJobDetail)
            .where(
                db.ParsedJobDetail.job_id == job.id,
                db.ParsedJobDetail.parser_type == "LLM",
            )
            .order_by(
                db.ParsedJobDetail.created_at.desc(),
                db.ParsedJobDetail.id.desc(),
            )
            .limit(1)
        )
        if llm_parsed_record is None:
            parsed_response = parse_job(
                session, job.id, "LLM", provider=llm_provider
            )
            llm_parsed_record = get_parsed_entity(session, parsed_response.id)
        parsed_record = llm_parsed_record
        context = _scoring_context(job, parsed_record, candidate, strategy)
        rejections = evaluate_hard_filters(context)
        if rejections:
            return _persist_hard_filtered_score(
                session,
                job,
                strategy,
                profile,
                parsed_record,
                context,
                rejections,
                reassessment_key,
            )
    base_fingerprint = _fingerprint(
        job.id,
        strategy,
        profile,
        parsed_record,
        llm_provider,
    )
    fingerprint = (
        hashlib.sha256(f"{base_fingerprint}:reassess:{reassessment_key}".encode()).hexdigest()
        if reassessment_key is not None
        else base_fingerprint
    )
    existing = session.scalar(
        select(db.JobScore).where(db.JobScore.input_fingerprint == fingerprint)
    )
    if existing:
        return _response(session, existing)
    try:
        llm_result = llm_provider.score_job(context)
    except LlmProviderError as exc:
        record_llm_invocation(
            session,
            user_id=DEFAULT_USER_ID,
            purpose="JOB_SCORE",
            input_hash=fingerprint,
            status="FAILED",
            metadata=LlmCallMetadata(
                provider=llm_provider.provider_name,
                model=llm_provider.model_name,
                prompt_version=llm_provider.prompt_version("score_job"),
                latency_ms=0,
                attempt_number=exc.attempt_number,
            ),
            failure_code=exc.code,
        )
        session.commit()
        raise
    try:
        result = validate_llm_score(context, llm_result.data)
    except LlmScoreValidationError:
        record_llm_invocation(
            session,
            user_id=DEFAULT_USER_ID,
            purpose="JOB_SCORE",
            input_hash=fingerprint,
            status="FAILED",
            metadata=llm_result.metadata,
            failure_code="INVALID_SCORING_OUTPUT",
        )
        session.commit()
        raise
    invocation = record_llm_invocation(
        session,
        user_id=DEFAULT_USER_ID,
        purpose="JOB_SCORE",
        input_hash=fingerprint,
        status="SUCCEEDED",
        metadata=llm_result.metadata,
    )
    automation_eligible = is_automation_eligible(
        result, llm_result.data.recommends_proactive_contact
    )
    snapshot = context.model_dump(mode="json")
    score = db.JobScore(
        job_id=job.id, strategy_id=strategy.id, candidate_profile_id=profile.id,
        parsed_job_detail_id=parsed_record.id, strategy_version=strategy.version,
        profile_version=profile.version, scoring_version=LLM_SCORING_VERSION,
        prompt_version=llm_result.metadata.prompt_version,
        llm_invocation_id=invocation.id,
        input_fingerprint=fingerprint, effective_job_status=result.effective_job_status.value,
        action_blockers=result.action_blockers, title_score=result.dimension_scores["title"],
        skill_score=result.dimension_scores["skills"], experience_score=result.dimension_scores["experience"],
        location_score=result.dimension_scores["location"], salary_score=result.dimension_scores["salary"],
        industry_score=result.dimension_scores["industry"], management_score=result.dimension_scores["management"],
        total_score=result.total_score, grade=result.grade.value, eligibility=result.eligibility.value,
        hard_rejected=result.hard_rejected, match_reasons=result.match_reasons,
        risk_notes=result.risk_notes, input_snapshot=snapshot,
        llm_recommends_proactive_contact=llm_result.data.recommends_proactive_contact,
        llm_contact_reason=llm_result.data.contact_reason,
        automation_eligible=automation_eligible,
        details=[db.JobScoreDetail(dimension=item.dimension, rule_code=item.rule_code,
                                   score_awarded=item.score, max_score=item.max_score,
                                   evidence_refs=item.evidence_refs,
                                   matched_facts=item.matched_facts, explanation=item.explanation,
                                   sort_order=index) for index, item in enumerate(result.details)],
        rejections=[db.JobRejection(rule_code=item.rule_code, message=item.message,
                                    evidence=item.evidence, sort_order=index)
                    for index, item in enumerate(result.rejection_reasons)],
    )
    session.add(score)
    session.commit()
    session.refresh(score)
    return _response(session, score)


def _scoring_context(
    job: db.Job,
    parsed_record: db.ParsedJobDetail,
    candidate: CandidateProfile,
    strategy: db.JobStrategy,
) -> ScoringContext:
    parsed_job = to_parsed_domain(parsed_record)
    domain_job = to_job_domain(job)
    if (
        parsed_job.part_time_detected
        and domain_job.work_mode.value == "ONSITE"
        and not parsed_job.onsite_required_explicitly
    ):
        domain_job = domain_job.model_copy(update={"work_mode": WorkMode.UNKNOWN})
    return with_evidence_catalog(
        ScoringContext(
            job=domain_job,
            parsed_job=parsed_job,
            candidate=candidate,
            strategy=strategy_to_domain(strategy),
        )
    )


def _persist_hard_filtered_score(
    session: Session,
    job: db.Job,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed_record: db.ParsedJobDetail,
    context: ScoringContext,
    rejections: list[RejectionReason],
    reassessment_key: str | None,
) -> ScoreResponse:
    fingerprint = _hard_filter_fingerprint(
        job.id,
        strategy,
        profile,
        parsed_record,
        reassessment_key,
    )
    existing = session.scalar(
        select(db.JobScore).where(db.JobScore.input_fingerprint == fingerprint)
    )
    if existing:
        return _response(session, existing)
    deterministic = score_job_deterministically(context)
    score = db.JobScore(
        job_id=job.id,
        strategy_id=strategy.id,
        candidate_profile_id=profile.id,
        parsed_job_detail_id=parsed_record.id,
        strategy_version=strategy.version,
        profile_version=profile.version,
        scoring_version=HARD_FILTER_SCORING_VERSION,
        prompt_version=HARD_FILTER_PROMPT_VERSION,
        llm_invocation_id=None,
        input_fingerprint=fingerprint,
        effective_job_status=deterministic.effective_job_status.value,
        action_blockers=deterministic.action_blockers,
        title_score=0,
        skill_score=0,
        experience_score=0,
        location_score=0,
        salary_score=0,
        industry_score=0,
        management_score=0,
        total_score=0,
        grade="C",
        eligibility="FILTERED_OUT",
        hard_rejected=True,
        match_reasons=[],
        risk_notes=["职位命中硬性排除规则，未调用大模型评分。"],
        input_snapshot=context.model_dump(mode="json"),
        llm_recommends_proactive_contact=False,
        llm_contact_reason="职位命中硬性排除规则，未调用大模型。",
        automation_eligible=False,
        details=[
            db.JobScoreDetail(
                dimension=dimension,
                rule_code="NOT_SCORED_HARD_FILTERED",
                score_awarded=0,
                max_score=max_score,
                evidence_refs=[],
                matched_facts={},
                explanation="职位已被硬性排除，未进行AI评分。",
                sort_order=index,
            )
            for index, (dimension, max_score) in enumerate(DIMENSION_MAX.items())
        ],
        rejections=[
            db.JobRejection(
                rule_code=item.rule_code,
                message=item.message,
                evidence=item.evidence,
                sort_order=index,
            )
            for index, item in enumerate(rejections)
        ],
    )
    session.add(score)
    session.commit()
    session.refresh(score)
    return _response(session, score)


def get_score(session: Session, score_id: object) -> ScoreResponse:
    score = session.get(db.JobScore, score_id)
    if score is None:
        raise ResourceNotFoundError("评分不存在")
    job = get_job_entity(session, score.job_id)
    if job.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("评分不存在")
    return _response(session, score)


def list_scores(
    session: Session, job_id: object, strategy_id: object | None, page: int, page_size: int
) -> tuple[list[ScoreResponse], int]:
    job = get_job_entity(session, job_id)
    query = select(db.JobScore).where(db.JobScore.job_id == job.id)
    if strategy_id is not None:
        query = query.where(db.JobScore.strategy_id == strategy_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(db.JobScore.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [_response(session, row) for row in rows], total


def _fingerprint(
    job_id: object,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed: db.ParsedJobDetail,
    provider: LlmProvider,
) -> str:
    payload = [str(job_id), str(strategy.id), strategy.version, str(profile.id), profile.version,
               str(parsed.id), LLM_SCORING_VERSION, provider.provider_name,
               provider.model_name, provider.prompt_version("score_job")]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _hard_filter_fingerprint(
    job_id: object,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed: db.ParsedJobDetail,
    reassessment_key: str | None,
) -> str:
    payload = [
        str(job_id),
        str(strategy.id),
        strategy.version,
        str(profile.id),
        profile.version,
        str(parsed.id),
        HARD_FILTER_SCORING_VERSION,
        reassessment_key,
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _response(session: Session, score: db.JobScore) -> ScoreResponse:
    details = [{"dimension": item.dimension, "score": item.score_awarded,
                "max_score": item.max_score, "rule_code": item.rule_code,
                "explanation": item.explanation, "evidence_refs": item.evidence_refs,
                "matched_facts": item.matched_facts}
               for item in sorted(score.details, key=lambda item: item.sort_order)]
    rejections = [{"rule_code": item.rule_code, "message": item.message,
                   "evidence": item.evidence}
                  for item in sorted(score.rejections, key=lambda item: item.sort_order)]
    dimensions = {"title": score.title_score, "skills": score.skill_score,
                  "experience": score.experience_score, "location": score.location_score,
                  "salary": score.salary_score, "industry": score.industry_score,
                  "management": score.management_score}
    invocation = (
        session.get(db.LlmInvocation, score.llm_invocation_id)
        if score.llm_invocation_id
        else None
    )
    return ScoreResponse(
        id=score.id, job_id=score.job_id, strategy_id=score.strategy_id,
        candidate_profile_id=score.candidate_profile_id,
        parsed_job_detail_id=score.parsed_job_detail_id,
        strategy_version=score.strategy_version, profile_version=score.profile_version,
        input_fingerprint=score.input_fingerprint, total_score=score.total_score,
        grade=score.grade, eligibility=score.eligibility, hard_rejected=score.hard_rejected,
        effective_job_status=score.effective_job_status, action_blockers=score.action_blockers,
        dimension_scores=dimensions, details=details, rejection_reasons=rejections,
        match_reasons=score.match_reasons, risk_notes=score.risk_notes,
        scoring_version=score.scoring_version, prompt_version=score.prompt_version,
        llm_invocation_id=score.llm_invocation_id,
        llm_provider=invocation.provider if invocation else None,
        llm_model=invocation.model if invocation else None,
        llm_recommends_proactive_contact=score.llm_recommends_proactive_contact,
        llm_contact_reason=score.llm_contact_reason,
        automation_eligible=score.automation_eligible,
    )
