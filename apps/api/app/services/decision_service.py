import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adapters.llm.errors import LlmProviderError
from apps.api.app.core.job_parser_config import get_job_parser_config
from apps.api.app.models import entities as db
from apps.api.app.schemas.decision import DecisionRequest, DecisionResponse
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.job_service import (
    get_job_entity,
    get_parsed_entity,
    llm_parse_fingerprint,
    parse_job,
    to_job_domain,
    to_parsed_domain,
)
from apps.api.app.services.llm_config_service import build_runtime_llm_provider
from apps.api.app.services.llm_service import record_llm_invocation
from apps.api.app.services.strategy_service import to_domain as strategy_to_domain
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.job_matching.decision import (
    DECISION_VERSION,
    build_llm_request,
    hard_filtered_result,
    validate_llm_decision,
)
from packages.job_matching.hard_filters import evaluate_hard_filters
from packages.job_matching.models import (
    CandidateProfile,
    CandidateSkill,
    HardRejectionReason,
    JobDecisionContext,
    JobDecisionResult,
)
from packages.job_matching.work_mode import infer_effective_work_mode
from packages.job_parser.models import WorkMode
from packages.llm.models import LlmCallMetadata
from packages.llm.ports import LlmProvider

HARD_FILTER_PROMPT_VERSION = "not-applicable"


def create_decision(
    session: Session,
    job_id: object,
    request: DecisionRequest,
    provider: LlmProvider | None = None,
    reassessment_key: str | None = None,
) -> DecisionResponse:
    job = get_job_entity(session, job_id)
    strategy = session.get(db.JobStrategy, request.strategy_id)
    profile = session.get(db.CandidateProfile, request.candidate_profile_id)
    if strategy is None or strategy.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("策略不存在")
    if profile is None or profile.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("候选人资料不存在")
    if not strategy.enabled:
        raise ValueError("已停用策略不能用于新决策")
    if strategy.candidate_profile_id != profile.id:
        raise ValueError("策略与候选人资料不匹配")

    candidate = _candidate(profile)
    parsed_record = _parsed_record(session, job, request)
    context = _decision_context(job, parsed_record, candidate, strategy)
    direction_keywords = _direction_keywords(context)
    rejections = evaluate_hard_filters(
        context, direction_keywords=direction_keywords
    )
    if rejections:
        return _persist_hard_filtered(
            session, job, strategy, profile, parsed_record, context, rejections,
            reassessment_key,
        )

    llm_provider = provider or build_runtime_llm_provider(session)
    if request.parsed_job_detail_id is None:
        parsed_record = _llm_parsed_record(session, job, llm_provider)
        context = _decision_context(job, parsed_record, candidate, strategy)
        rejections = evaluate_hard_filters(
            context, direction_keywords=direction_keywords
        )
        if rejections:
            return _persist_hard_filtered(
                session, job, strategy, profile, parsed_record, context, rejections,
                reassessment_key,
            )

    fingerprint = _fingerprint(
        job.id, strategy, profile, parsed_record, llm_provider, reassessment_key
    )
    existing = session.scalar(
        select(db.JobDecision).where(db.JobDecision.input_fingerprint == fingerprint)
    )
    if existing:
        return _response(session, existing)

    try:
        llm_result = llm_provider.decide_job_contact(build_llm_request(context))
    except LlmProviderError as exc:
        record_llm_invocation(
            session,
            user_id=DEFAULT_USER_ID,
            purpose="JOB_CONTACT_DECISION",
            input_hash=fingerprint,
            status="FAILED",
            metadata=LlmCallMetadata(
                provider=llm_provider.provider_name,
                model=llm_provider.model_name,
                prompt_version=llm_provider.prompt_version("decide_job_contact"),
                latency_ms=0,
                attempt_number=exc.attempt_number,
            ),
            failure_code=exc.code,
        )
        session.commit()
        raise

    result = validate_llm_decision(context, llm_result.data)
    invocation = record_llm_invocation(
        session,
        user_id=DEFAULT_USER_ID,
        purpose="JOB_CONTACT_DECISION",
        input_hash=fingerprint,
        status="SUCCEEDED",
        metadata=llm_result.metadata,
    )
    decision = _entity(
        job, strategy, profile, parsed_record, fingerprint, result,
        context.model_dump(mode="json"), llm_result.metadata.prompt_version, invocation.id,
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return _response(session, decision)


def _direction_keywords(context: JobDecisionContext) -> list[str]:
    configured = get_job_parser_config().relevant_title_keywords
    strategy_patterns = [
        rule.pattern
        for rule in context.strategy.title_rules
        if rule.rule_type.value == "INCLUDE"
    ]
    return list(dict.fromkeys([*configured, *strategy_patterns]))


def get_decision(session: Session, decision_id: object) -> DecisionResponse:
    decision = session.get(db.JobDecision, decision_id)
    if decision is None or get_job_entity(session, decision.job_id).user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("职位沟通决策不存在")
    return _response(session, decision)


def list_decisions(
    session: Session, job_id: object, strategy_id: object | None, page: int, page_size: int
) -> tuple[list[DecisionResponse], int]:
    job = get_job_entity(session, job_id)
    query = select(db.JobDecision).where(db.JobDecision.job_id == job.id)
    if strategy_id is not None:
        query = query.where(db.JobDecision.strategy_id == strategy_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(db.JobDecision.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [_response(session, row) for row in rows], total


def _candidate(profile: db.CandidateProfile) -> CandidateProfile:
    return CandidateProfile(
        name=profile.name,
        total_years=profile.total_years,
        management_years=profile.management_years,
        has_architecture_experience=profile.has_architecture_experience,
        has_core_system_experience=profile.has_core_system_experience,
        bachelor_full_time=profile.bachelor_full_time,
        skills=[
            CandidateSkill(
                name=item.name, years=item.years, source=item.source, is_core=item.is_core
            )
            for item in profile.skills
        ],
        industry_experiences=[item.industry_code for item in profile.industries],
        version=profile.version,
    )


def _parsed_record(
    session: Session, job: db.Job, request: DecisionRequest
) -> db.ParsedJobDetail:
    if request.parsed_job_detail_id:
        parsed = get_parsed_entity(session, request.parsed_job_detail_id)
        if parsed.job_id != job.id:
            raise ValueError("解析记录不属于当前职位")
        return parsed
    existing = session.scalar(
        select(db.ParsedJobDetail)
        .where(
            db.ParsedJobDetail.job_id == job.id,
            db.ParsedJobDetail.parser_type == "RULE",
        )
        .order_by(db.ParsedJobDetail.created_at.desc(), db.ParsedJobDetail.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    return get_parsed_entity(session, parse_job(session, job.id, "RULE").id)


def _llm_parsed_record(
    session: Session, job: db.Job, provider: LlmProvider
) -> db.ParsedJobDetail:
    fingerprint = llm_parse_fingerprint(session, job, provider)
    parsed = session.scalar(
        select(db.ParsedJobDetail)
        .where(
            db.ParsedJobDetail.job_id == job.id,
            db.ParsedJobDetail.parser_type == "LLM",
            db.ParsedJobDetail.input_fingerprint == fingerprint,
        )
        .order_by(db.ParsedJobDetail.created_at.desc(), db.ParsedJobDetail.id.desc())
        .limit(1)
    )
    if parsed is None:
        parsed = get_parsed_entity(
            session, parse_job(session, job.id, "LLM", provider=provider).id
        )
    return parsed


def _decision_context(
    job: db.Job,
    parsed: db.ParsedJobDetail,
    candidate: CandidateProfile,
    strategy: db.JobStrategy,
) -> JobDecisionContext:
    parsed_job = to_parsed_domain(parsed)
    domain_job = to_job_domain(job)
    accepted_location_independent_part_time = (
        parsed_job.part_time_detected
        and strategy.accept_part_time
        and domain_job.work_mode.value == "ONSITE"
        and not parsed_job.onsite_required_explicitly
    )
    if accepted_location_independent_part_time:
        effective_mode = WorkMode.UNKNOWN
    else:
        effective_mode = infer_effective_work_mode(
            domain_job.work_mode,
            title=domain_job.title,
            description=domain_job.description,
            location=domain_job.location,
            infer_onsite_from_location=not (
                parsed_job.part_time_detected
                and strategy.accept_part_time
                and not parsed_job.onsite_required_explicitly
            ),
        )
    if effective_mode is not domain_job.work_mode:
        domain_job = domain_job.model_copy(update={"work_mode": effective_mode})
    return JobDecisionContext(
        job=domain_job,
        parsed_job=parsed_job,
        candidate=candidate,
        strategy=strategy_to_domain(strategy),
    )


def _persist_hard_filtered(
    session: Session,
    job: db.Job,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed: db.ParsedJobDetail,
    context: JobDecisionContext,
    rejections: list[HardRejectionReason],
    reassessment_key: str | None,
) -> DecisionResponse:
    fingerprint = _hard_filter_fingerprint(
        job.id, strategy, profile, parsed, reassessment_key
    )
    existing = session.scalar(
        select(db.JobDecision).where(db.JobDecision.input_fingerprint == fingerprint)
    )
    if existing:
        return _response(session, existing)
    result = hard_filtered_result(context, rejections)
    decision = _entity(
        job, strategy, profile, parsed, fingerprint, result,
        context.model_dump(mode="json"), HARD_FILTER_PROMPT_VERSION, None,
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return _response(session, decision)


def _entity(
    job: db.Job,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed: db.ParsedJobDetail,
    fingerprint: str,
    result: JobDecisionResult,
    snapshot: dict[str, object],
    prompt_version: str,
    invocation_id: object | None,
) -> db.JobDecision:
    return db.JobDecision(
        job_id=job.id,
        strategy_id=strategy.id,
        candidate_profile_id=profile.id,
        parsed_job_detail_id=parsed.id,
        strategy_version=strategy.version,
        profile_version=profile.version,
        decision_version=result.decision_version,
        prompt_version=prompt_version,
        llm_invocation_id=invocation_id,
        input_fingerprint=fingerprint,
        effective_job_status=result.effective_job_status.value,
        action_blockers=result.action_blockers,
        decision=result.decision.value,
        confidence=result.confidence,
        hard_rejected=result.hard_rejected,
        reason=result.reason,
        automation_eligible=result.automation_eligible,
        matched_evidence=result.matched_evidence,
        uncertainties=result.uncertainties,
        rejection_reasons=[item.model_dump(mode="json") for item in result.rejection_reasons],
        input_snapshot=snapshot,
    )


def _fingerprint(
    job_id: object,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed: db.ParsedJobDetail,
    provider: LlmProvider,
    reassessment_key: str | None,
) -> str:
    payload = [
        str(job_id), str(strategy.id), strategy.version, str(profile.id), profile.version,
        str(parsed.id), DECISION_VERSION, provider.provider_name, provider.model_name,
        provider.prompt_version("decide_job_contact"), reassessment_key,
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _hard_filter_fingerprint(
    job_id: object,
    strategy: db.JobStrategy,
    profile: db.CandidateProfile,
    parsed: db.ParsedJobDetail,
    reassessment_key: str | None,
) -> str:
    payload = [
        str(job_id), str(strategy.id), strategy.version, str(profile.id), profile.version,
        str(parsed.id), DECISION_VERSION, "HARD_FILTER", reassessment_key,
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _response(session: Session, decision: db.JobDecision) -> DecisionResponse:
    invocation = (
        session.get(db.LlmInvocation, decision.llm_invocation_id)
        if decision.llm_invocation_id else None
    )
    return DecisionResponse(
        id=decision.id,
        job_id=decision.job_id,
        strategy_id=decision.strategy_id,
        candidate_profile_id=decision.candidate_profile_id,
        parsed_job_detail_id=decision.parsed_job_detail_id,
        strategy_version=decision.strategy_version,
        profile_version=decision.profile_version,
        input_fingerprint=decision.input_fingerprint,
        decision=decision.decision,
        confidence=decision.confidence,
        hard_rejected=decision.hard_rejected,
        effective_job_status=decision.effective_job_status,
        action_blockers=decision.action_blockers,
        rejection_reasons=decision.rejection_reasons,
        matched_evidence=decision.matched_evidence,
        uncertainties=decision.uncertainties,
        reason=decision.reason,
        automation_eligible=decision.automation_eligible,
        decision_version=decision.decision_version,
        prompt_version=decision.prompt_version,
        llm_invocation_id=decision.llm_invocation_id,
        llm_provider=invocation.provider if invocation else None,
        llm_model=invocation.model if invocation else None,
    )
