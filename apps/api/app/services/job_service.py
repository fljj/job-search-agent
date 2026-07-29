import hashlib
import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

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
from packages.llm.models import LlmCallMetadata
from packages.llm.ports import LlmProvider


def import_job(session: Session, payload: JobImportPayload) -> JobImportResponse:
    ensure_default_user(session)
    content_hash = _content_hash(payload)
    filters: list[Any] = [db.Job.content_hash == content_hash]
    if payload.external_job_id:
        filters.append(db.Job.external_job_id == payload.external_job_id)
    existing = session.scalar(select(db.Job).where(
        db.Job.user_id == DEFAULT_USER_ID,
        db.Job.source == payload.source,
        or_(*filters),
    ))
    if existing:
        return JobImportResponse(result="DUPLICATE", job=_response(existing))
    job = db.Job(
        user_id=DEFAULT_USER_ID, content_hash=content_hash,
        raw_data=payload.model_dump(mode="json"),
        **payload.model_dump(exclude={"published_at", "work_mode", "source_status"}),
        published_at=payload.published_at, work_mode=payload.work_mode.value,
        source_status=payload.source_status.value,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return JobImportResponse(result="CREATED", job=_response(job))


def list_jobs(
    session: Session,
    page: int,
    page_size: int,
    job_id: object | None = None,
    strategy_id: object | None = None,
    grade: str | None = None,
    eligibility: str | None = None,
    effective_job_status: str | None = None,
    work_mode: str | None = None,
    hard_rejected: bool | None = None,
) -> tuple[list[JobResponse], int]:
    query = select(db.Job).where(db.Job.user_id == DEFAULT_USER_ID)
    if job_id is not None:
        query = query.where(db.Job.id == job_id)
    if work_mode:
        query = query.where(db.Job.work_mode == work_mode)
    jobs = session.scalars(
        query.order_by(db.Job.created_at.desc(), db.Job.id.desc())
    ).all()
    items: list[JobResponse] = []
    for job in jobs:
        latest_score = None
        if strategy_id is not None:
            latest_score = session.scalar(
                select(db.JobScore)
                .where(db.JobScore.job_id == job.id, db.JobScore.strategy_id == strategy_id)
                .order_by(db.JobScore.created_at.desc(), db.JobScore.id.desc())
                .limit(1)
            )
            if latest_score is None:
                continue
            if grade and latest_score.grade != grade:
                continue
            if eligibility and latest_score.eligibility != eligibility:
                continue
            if effective_job_status and latest_score.effective_job_status != effective_job_status:
                continue
            if hard_rejected is not None and latest_score.hard_rejected != hard_rejected:
                continue
        elif any((grade, eligibility, effective_job_status, hard_rejected is not None)):
            raise ValueError("评分结果筛选必须同时提供 strategy_id")
        summary = None if latest_score is None else {
            "id": str(latest_score.id), "total_score": latest_score.total_score,
            "grade": latest_score.grade, "eligibility": latest_score.eligibility,
            "hard_rejected": latest_score.hard_rejected,
            "effective_job_status": latest_score.effective_job_status,
        }
        items.append(
            _response(
                job,
                summary,
                _communication_summary(session, job, latest_score),
            )
        )
    total = len(items)
    start = (page - 1) * page_size
    return items[start:start + page_size], total


def get_job(session: Session, job_id: object) -> JobResponse:
    job = _get_job_entity(session, job_id)
    return _response(job)


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
        record_llm_invocation(
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
        external_job_id=job.external_job_id, title=job.title, company_name=job.company_name,
        industry=job.industry, location=job.location, work_mode=job.work_mode,
        salary_text=job.salary_text, description=job.description, published_at=job.published_at,
        source_status=job.source_status, source=job.source,
    )


def _response(
    job: db.Job,
    latest_score: dict[str, object] | None = None,
    communication: dict[str, object] | None = None,
) -> JobResponse:
    return JobResponse(
        id=job.id,
        content_hash=job.content_hash,
        latest_score=latest_score,
        communication=communication,
        **_domain_job(job).model_dump(),
    )


def _communication_summary(
    session: Session,
    job: db.Job,
    latest_score: db.JobScore | None,
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
            db.ActionQueue.action_type == "GREETING",
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
    elif latest_score is not None and latest_score.automation_eligible:
        status = "READY_TO_CONTACT"
    else:
        status = "NOT_CONTACTED"
    return {
        "status": status,
        "conversation_id": conversation.id if conversation else None,
        "action_id": action.id if action else None,
        "action_status": action.status if action else None,
        "failure_code": action.failure_code if action else None,
        "reason_codes": record.reason_codes if record else [],
    }


def _parsed_response(record: db.ParsedJobDetail) -> ParsedJobResponse:
    return ParsedJobResponse(id=record.id, job_id=record.job_id, **to_parsed_domain(record).model_dump())


def _content_hash(payload: JobImportPayload) -> str:
    stable = payload.model_dump(mode="json", exclude={"external_job_id", "published_at"})
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
