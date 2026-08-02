import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from adapters.llm.errors import LlmProviderError
from apps.api.app.core.job_parser_config import get_job_parser_config
from apps.api.app.models import entities as db
from apps.api.app.schemas.job import (
    JobImportPayload,
    JobImportResponse,
    JobResponse,
    ParsedJobResponse,
)
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.llm_config_service import build_runtime_llm_provider
from apps.api.app.services.llm_service import record_llm_invocation
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.job_parser.models import JobInput, ParsedJob
from packages.job_parser.rule_parser import RuleJobParser
from packages.job_parser.source_url import normalize_job_source_url
from packages.llm.models import LlmCallMetadata
from packages.llm.ports import LlmProvider
from packages.policy_engine.state_machine import ActionType


def import_job(session: Session, payload: JobImportPayload) -> JobImportResponse:
    ensure_default_user(session)
    payload = payload.model_copy(
        update={"source_url": normalize_job_source_url(payload.source, payload.source_url)}
    )
    content_hash = _content_hash(payload)
    existing_by_external = None
    if payload.external_job_id:
        existing_by_external = session.scalar(select(db.Job).where(
            db.Job.user_id == DEFAULT_USER_ID,
            db.Job.source == payload.source,
            db.Job.external_job_id == payload.external_job_id,
        ))
    existing_by_content = session.scalar(select(db.Job).where(
        db.Job.user_id == DEFAULT_USER_ID,
        db.Job.source == payload.source,
        db.Job.content_hash == content_hash,
    ))
    if (
        existing_by_external is not None
        and existing_by_content is not None
        and existing_by_external.id != existing_by_content.id
    ):
        _record_source_url(existing_by_content, payload.source_url)
        _record_job_observation(session, existing_by_content)
        session.commit()
        return JobImportResponse(result="DUPLICATE", job=_response(existing_by_content))
    existing = existing_by_external or existing_by_content
    if existing:
        if existing.content_hash != content_hash and existing_by_external is not None:
            _record_job_observation(session, existing)
            values = payload.model_dump(exclude={"published_at", "work_mode", "source_status"})
            for key, value in values.items():
                if key != "external_job_id":
                    setattr(existing, key, value)
            existing.content_hash = content_hash
            existing.published_at = payload.published_at
            existing.work_mode = payload.work_mode.value
            existing.source_status = payload.source_status.value
            existing.raw_data = payload.model_dump(mode="json")
            _record_source_url(existing, payload.source_url)
            _record_job_observation(session, existing)
            session.commit()
            session.refresh(existing)
            return JobImportResponse(result="UPDATED", job=_response(existing))
        _record_source_url(existing, payload.source_url)
        _record_job_observation(session, existing)
        session.commit()
        return JobImportResponse(result="DUPLICATE", job=_response(existing))
    job = db.Job(
        user_id=DEFAULT_USER_ID, content_hash=content_hash,
        raw_data=payload.model_dump(mode="json"),
        **payload.model_dump(exclude={"published_at", "work_mode", "source_status"}),
        published_at=payload.published_at, work_mode=payload.work_mode.value,
        source_status=payload.source_status.value,
    )
    _record_source_url(job, payload.source_url)
    session.add(job)
    session.flush()
    _record_job_observation(session, job)
    session.commit()
    session.refresh(job)
    return JobImportResponse(result="CREATED", job=_response(job))


def _record_job_observation(session: Session, job: db.Job) -> None:
    if session.scalar(select(db.JobObservation.id).where(
        db.JobObservation.job_id == job.id,
        db.JobObservation.content_hash == job.content_hash,
    )):
        return
    session.add(db.JobObservation(
        job_id=job.id,
        content_hash=job.content_hash,
        snapshot={
            "title": job.title,
            "company_name": job.company_name,
            "industry": job.industry,
            "location": job.location,
            "work_mode": job.work_mode,
            "salary_text": job.salary_text,
            "description": job.description,
            "source_status": job.source_status,
            "recruiter_role": job.recruiter_role,
            "raw_data": job.raw_data,
        },
    ))


def _record_source_url(job: db.Job, source_url: str | None) -> None:
    if source_url:
        job.source_url = source_url
        job.source_url_observed_at = datetime.now(UTC)


def list_jobs(
    session: Session,
    page: int,
    page_size: int,
    job_id: object | None = None,
    strategy_id: object | None = None,
    decision: str | None = None,
    effective_job_status: str | None = None,
    work_mode: str | None = None,
    hard_rejected: bool | None = None,
    keyword: str | None = None,
) -> tuple[list[JobResponse], int]:
    decision_rank = (
        select(
            db.JobDecision.id.label("decision_id"),
            db.JobDecision.job_id.label("job_id"),
            func.row_number().over(
                partition_by=db.JobDecision.job_id,
                order_by=(db.JobDecision.created_at.desc(), db.JobDecision.id.desc()),
            ).label("rank"),
        )
        .where(db.JobDecision.strategy_id == strategy_id)
        .subquery()
        if strategy_id is not None
        else None
    )
    latest_decision = aliased(db.JobDecision)
    query = (
        select(db.Job, latest_decision)
        if decision_rank is not None
        else select(db.Job)
    ).where(db.Job.user_id == DEFAULT_USER_ID)
    if decision_rank is not None:
        query = query.join(
            decision_rank,
            and_(decision_rank.c.job_id == db.Job.id, decision_rank.c.rank == 1),
        ).join(latest_decision, latest_decision.id == decision_rank.c.decision_id)
    if job_id is not None:
        query = query.where(db.Job.id == job_id)
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.where(
            or_(db.Job.title.ilike(pattern), db.Job.company_name.ilike(pattern))
        )
    if work_mode:
        query = query.where(db.Job.work_mode == work_mode)
    if decision:
        query = query.where(latest_decision.decision == decision)
    if effective_job_status:
        query = query.where(latest_decision.effective_job_status == effective_job_status)
    if hard_rejected is not None:
        query = query.where(latest_decision.hard_rejected == hard_rejected)
    if strategy_id is None and any((decision, effective_job_status, hard_rejected is not None)):
        raise ValueError("职位沟通决策筛选必须同时提供 strategy_id")
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(
        query.order_by(db.Job.created_at.desc(), db.Job.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    jobs = [row[0] for row in rows]
    communication_rows = _communication_rows(session, jobs)
    items: list[JobResponse] = []
    for row in rows:
        job = row[0]
        decision_record = row[1] if decision_rank is not None else None
        summary = None if decision_record is None else {
            "id": str(decision_record.id),
            "decision": decision_record.decision,
            "confidence": float(decision_record.confidence),
            "hard_rejected": decision_record.hard_rejected,
            "effective_job_status": decision_record.effective_job_status,
            "reason": decision_record.reason,
            "matched_evidence": decision_record.matched_evidence,
            "uncertainties": decision_record.uncertainties,
            "automation_eligible": decision_record.automation_eligible,
        }
        items.append(
            _response(
                job,
                summary,
                _build_communication_summary(
                    decision_record,
                    communication_rows[0].get(job.id),
                    communication_rows[1].get(job.id),
                    communication_rows[2].get(job.id),
                ),
            )
        )
    return items, total


def get_job(session: Session, job_id: object) -> JobResponse:
    job = _get_job_entity(session, job_id)
    return _response(job)


LLM_PARSE_SCHEMA_VERSION = "job-parse-schema:1.0.0"


def parse_job(
    session: Session,
    job_id: object,
    mode: str,
    *,
    provider: LlmProvider | None = None,
) -> ParsedJobResponse:
    job = _get_job_entity(session, job_id)
    domain_job = _domain_job(job)
    normalized_mode = mode.upper()
    if normalized_mode == "RULE":
        parsed = RuleJobParser(get_job_parser_config()).parse(domain_job)
    elif normalized_mode == "LLM":
        llm_provider = provider or build_runtime_llm_provider(session)
        parse_fingerprint = llm_parse_fingerprint(session, job, llm_provider)
        existing = session.scalar(
            select(db.ParsedJobDetail).where(
                db.ParsedJobDetail.input_fingerprint == parse_fingerprint
            )
        )
        if existing is not None:
            return _parsed_response(existing)
        try:
            llm_result = llm_provider.parse_job(domain_job)
        except LlmProviderError as exc:
            record_llm_invocation(
                session,
                user_id=DEFAULT_USER_ID,
                purpose="JOB_PARSE",
                input_hash=job.content_hash,
                status="FAILED",
                metadata=LlmCallMetadata(
                    provider=llm_provider.provider_name,
                    model=llm_provider.model_name,
                    prompt_version=llm_provider.prompt_version("parse_job"),
                    latency_ms=0,
                    attempt_number=exc.attempt_number,
                ),
                failure_code=exc.code,
            )
            session.commit()
            raise
        deterministic_flags = RuleJobParser(get_job_parser_config()).parse(domain_job)
        parsed = llm_result.data.model_copy(
            update={
                "parser_type": "LLM",
                "parser_version": llm_result.metadata.prompt_version,
                "outsourcing_detected": deterministic_flags.outsourcing_detected,
                "headhunter_detected": deterministic_flags.headhunter_detected,
                "internship_detected": deterministic_flags.internship_detected,
                "full_time_bachelor_required": (
                    deterministic_flags.full_time_bachelor_required
                ),
                "part_time_detected": deterministic_flags.part_time_detected,
                "onsite_required_explicitly": (
                    deterministic_flags.onsite_required_explicitly
                ),
            }
        )
        invocation = record_llm_invocation(
            session,
            user_id=DEFAULT_USER_ID,
            purpose="JOB_PARSE",
            input_hash=job.content_hash,
            status="SUCCEEDED",
            metadata=llm_result.metadata,
        )
    else:
        raise ValueError("解析模式只支持 RULE 或 LLM")
    record = db.ParsedJobDetail(
        job_id=job.id, parser_type=parsed.parser_type, parser_version=parsed.parser_version,
        input_fingerprint=(parse_fingerprint if normalized_mode == "LLM" else None),
        provider=(llm_provider.provider_name if normalized_mode == "LLM" else None),
        model=(llm_provider.model_name if normalized_mode == "LLM" else None),
        prompt_version=(
            llm_provider.prompt_version("parse_job") if normalized_mode == "LLM" else None
        ),
        schema_version=(LLM_PARSE_SCHEMA_VERSION if normalized_mode == "LLM" else None),
        llm_invocation_id=(invocation.id if normalized_mode == "LLM" else None),
        required_skills=parsed.required_skills, preferred_skills=parsed.preferred_skills,
        years_required=parsed.years_required, management_required=parsed.management_required,
        architecture_required=parsed.architecture_required,
        seniority_level=parsed.seniority_level.value, responsibilities=parsed.responsibilities,
        salary_normalized=parsed.salary.model_dump(mode="json") if parsed.salary else None,
        flags={"outsourcing_detected": parsed.outsourcing_detected,
               "headhunter_detected": parsed.headhunter_detected,
               "internship_detected": parsed.internship_detected,
               "full_time_bachelor_required": parsed.full_time_bachelor_required,
               "part_time_detected": parsed.part_time_detected,
               "onsite_required_explicitly": parsed.onsite_required_explicitly},
        confidence=parsed.confidence, warnings=parsed.warnings,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return _parsed_response(record)


def llm_parse_fingerprint(
    session: Session,
    job: db.Job,
    provider: LlmProvider,
) -> str:
    profile_versions = session.execute(
        select(db.CandidateProfile.id, db.CandidateProfile.version).where(
            db.CandidateProfile.user_id == DEFAULT_USER_ID
        )
    ).all()
    strategy_versions = session.execute(
        select(db.JobStrategy.id, db.JobStrategy.version).where(
            db.JobStrategy.user_id == DEFAULT_USER_ID,
            db.JobStrategy.enabled.is_(True),
        )
    ).all()
    payload = {
        "job_content_hash": job.content_hash,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "prompt_version": provider.prompt_version("parse_job"),
        "schema_version": LLM_PARSE_SCHEMA_VERSION,
        "profile_versions": sorted((str(item[0]), item[1]) for item in profile_versions),
        "strategy_versions": sorted((str(item[0]), item[1]) for item in strategy_versions),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def get_parsed_entity(session: Session, parsed_id: object) -> db.ParsedJobDetail:
    record = session.get(db.ParsedJobDetail, parsed_id)
    if record is None:
        raise ResourceNotFoundError("解析记录不存在")
    job = _get_job_entity(session, record.job_id)
    if job.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("解析记录不存在")
    return record


def list_parsed_details(
    session: Session, job_id: object, page: int, page_size: int
) -> tuple[list[ParsedJobResponse], int]:
    job = _get_job_entity(session, job_id)
    query = select(db.ParsedJobDetail).where(db.ParsedJobDetail.job_id == job.id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(
            db.ParsedJobDetail.created_at.desc(),
            db.ParsedJobDetail.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [_parsed_response(row) for row in rows], total


def get_parsed_detail(session: Session, job_id: object, parsed_id: object) -> ParsedJobResponse:
    job = _get_job_entity(session, job_id)
    record = get_parsed_entity(session, parsed_id)
    if record.job_id != job.id:
        raise ResourceNotFoundError("解析记录不存在")
    return _parsed_response(record)


def to_parsed_domain(record: db.ParsedJobDetail) -> ParsedJob:
    flags = record.flags or {}
    return ParsedJob(
        required_skills=record.required_skills, preferred_skills=record.preferred_skills,
        years_required=record.years_required, management_required=record.management_required,
        architecture_required=record.architecture_required, seniority_level=record.seniority_level,
        responsibilities=record.responsibilities, salary=record.salary_normalized,
        outsourcing_detected=flags.get("outsourcing_detected", False),
        headhunter_detected=flags.get("headhunter_detected", False),
        internship_detected=flags.get("internship_detected", False),
        full_time_bachelor_required=flags.get(
            "full_time_bachelor_required", False
        ),
        part_time_detected=flags.get("part_time_detected", False),
        onsite_required_explicitly=flags.get(
            "onsite_required_explicitly", False
        ),
        confidence=record.confidence,
        warnings=record.warnings, parser_type=record.parser_type, parser_version=record.parser_version,
    )


def get_job_entity(session: Session, job_id: object) -> db.Job:
    return _get_job_entity(session, job_id)


def to_job_domain(job: db.Job) -> JobInput:
    return _domain_job(job)


def _get_job_entity(session: Session, job_id: object) -> db.Job:
    job = session.get(db.Job, job_id)
    if job is None or job.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("职位不存在")
    return job


def _domain_job(job: db.Job) -> JobInput:
    return JobInput(
        external_job_id=job.external_job_id, source_url=job.source_url,
        title=job.title, company_name=job.company_name,
        industry=job.industry, location=job.location, work_mode=job.work_mode,
        salary_text=job.salary_text, description=job.description, published_at=job.published_at,
        source_status=job.source_status, source=job.source,
        recruiter_role=job.recruiter_role,
    )


def _response(
    job: db.Job,
    latest_decision: dict[str, object] | None = None,
    communication: dict[str, object] | None = None,
) -> JobResponse:
    return JobResponse(
        id=job.id,
        content_hash=job.content_hash,
        latest_decision=latest_decision,
        communication=communication,
        **_domain_job(job).model_dump(),
    )


def _communication_summary(
    session: Session,
    job: db.Job,
    latest_decision: db.JobDecision | None,
) -> dict[str, object]:
    conversation = session.scalar(
        select(db.Conversation)
        .where(db.Conversation.job_id == job.id)
        .order_by(db.Conversation.created_at.desc())
        .limit(1)
    )
    action = session.scalar(
        select(db.ActionQueue)
        .where(
            db.ActionQueue.job_id == job.id,
            db.ActionQueue.action_type == ActionType.GREETING.value,
        )
        .order_by(db.ActionQueue.created_at.desc())
        .limit(1)
    )
    record = session.scalar(
        select(db.JobDiscoveryRecord)
        .where(db.JobDiscoveryRecord.job_id == job.id)
        .order_by(db.JobDiscoveryRecord.updated_at.desc())
        .limit(1)
    )
    return _build_communication_summary(latest_decision, conversation, action, record)


def _communication_rows(
    session: Session, jobs: list[db.Job]
) -> tuple[
    dict[object, db.Conversation],
    dict[object, db.ActionQueue],
    dict[object, db.JobDiscoveryRecord],
]:
    job_ids = [job.id for job in jobs]
    if not job_ids:
        return {}, {}, {}

    def latest_by_job(rows: list[object]) -> dict[object, object]:
        result: dict[object, object] = {}
        for item in rows:
            result.setdefault(item.job_id, item)  # type: ignore[attr-defined]
        return result

    conversations = latest_by_job(list(session.scalars(
        select(db.Conversation).where(db.Conversation.job_id.in_(job_ids)).order_by(
            db.Conversation.created_at.desc(), db.Conversation.id.desc()
        )
    ).all()))
    actions = latest_by_job(list(session.scalars(
        select(db.ActionQueue).where(
            db.ActionQueue.job_id.in_(job_ids),
            db.ActionQueue.action_type == ActionType.GREETING.value,
        ).order_by(db.ActionQueue.created_at.desc(), db.ActionQueue.id.desc())
    ).all()))
    records = latest_by_job(list(session.scalars(
        select(db.JobDiscoveryRecord).where(
            db.JobDiscoveryRecord.job_id.in_(job_ids)
        ).order_by(db.JobDiscoveryRecord.updated_at.desc(), db.JobDiscoveryRecord.id.desc())
    ).all()))
    return conversations, actions, records  # type: ignore[return-value]


def _build_communication_summary(
    latest_decision: db.JobDecision | None,
    conversation: db.Conversation | None,
    action: db.ActionQueue | None,
    record: db.JobDiscoveryRecord | None,
) -> dict[str, object]:
    record_decision_id = getattr(record, "job_decision_id", None)
    latest_decision_id = getattr(latest_decision, "id", None)
    record_is_stale = bool(
        record is not None
        and latest_decision is not None
        and record_decision_id is not None
        and latest_decision_id is not None
        and record_decision_id != latest_decision_id
    )
    effective_record = None if record_is_stale else record
    if conversation is not None:
        status = "CONVERSATION_ACTIVE"
    elif action is not None and action.status == "SUCCEEDED":
        status = "GREETING_SENT_PENDING_SYNC"
    elif action is not None and action.status == "FAILED_RETRYABLE":
        status = "GREETING_RETRY_PENDING"
    elif action is not None and action.status == "OUTCOME_UNKNOWN":
        status = "GREETING_OUTCOME_UNKNOWN"
    elif action is not None and action.status in {
        "APPROVED",
        "EXECUTING",
        "PENDING_APPROVAL",
    }:
        status = "GREETING_IN_PROGRESS"
    elif action is not None:
        status = "GREETING_FAILED"
    elif effective_record is not None and effective_record.status == "SKIPPED":
        status = "NOT_CONTACTED"
    elif latest_decision is not None and latest_decision.automation_eligible:
        status = "READY_TO_CONTACT"
    else:
        status = "NOT_CONTACTED"
    reason_codes = effective_record.reason_codes if effective_record else [
        str(item["rule_code"])
        for item in (getattr(latest_decision, "rejection_reasons", None) or [])
        if isinstance(item, dict) and item.get("rule_code")
    ]
    return {
        "status": status,
        "conversation_id": conversation.id if conversation else None,
        "action_id": action.id if action else None,
        "action_status": action.status if action else None,
        "failure_code": action.failure_code if action else None,
        "reason_codes": reason_codes,
    }


def _parsed_response(record: db.ParsedJobDetail) -> ParsedJobResponse:
    return ParsedJobResponse(id=record.id, job_id=record.job_id, **to_parsed_domain(record).model_dump())


def _content_hash(payload: JobImportPayload) -> str:
    stable = payload.model_dump(
        mode="json", exclude={"external_job_id", "published_at", "source_url"}
    )
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
