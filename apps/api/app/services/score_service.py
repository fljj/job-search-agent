import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
from apps.api.app.services.strategy_service import to_domain as strategy_to_domain
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.scoring.engine import SCORING_VERSION, score_job
from packages.scoring.models import CandidateProfile, CandidateSkill, ScoringContext


def create_score(session: Session, job_id: object, request: ScoreRequest) -> ScoreResponse:
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
    if request.parsed_job_detail_id:
        parsed_record = get_parsed_entity(session, request.parsed_job_detail_id)
        if parsed_record.job_id != job.id:
            raise ValueError("解析记录不属于当前职位")
    else:
        parsed_response = parse_job(session, job.id, "RULE")
        parsed_record = get_parsed_entity(session, parsed_response.id)
    fingerprint = _fingerprint(job.id, strategy, profile, parsed_record)
    existing = session.scalar(select(db.JobScore).where(db.JobScore.input_fingerprint == fingerprint))
    if existing:
        return _response(existing)
    candidate = CandidateProfile(
        name=profile.name, total_years=profile.total_years,
        management_years=profile.management_years,
        has_architecture_experience=profile.has_architecture_experience,
        has_core_system_experience=profile.has_core_system_experience,
        skills=[CandidateSkill(name=item.name, years=item.years, source=item.source,
                               is_core=item.is_core) for item in profile.skills],
        industry_experiences=[item.industry_code for item in profile.industries],
        version=profile.version,
    )
    context = ScoringContext(job=to_job_domain(job), parsed_job=to_parsed_domain(parsed_record),
                             candidate=candidate, strategy=strategy_to_domain(strategy))
    result = score_job(context)
    snapshot = context.model_dump(mode="json")
    score = db.JobScore(
        job_id=job.id, strategy_id=strategy.id, candidate_profile_id=profile.id,
        parsed_job_detail_id=parsed_record.id, strategy_version=strategy.version,
        profile_version=profile.version, scoring_version=SCORING_VERSION,
        input_fingerprint=fingerprint, effective_job_status=result.effective_job_status.value,
        action_blockers=result.action_blockers, title_score=result.dimension_scores["title"],
        skill_score=result.dimension_scores["skills"], experience_score=result.dimension_scores["experience"],
        location_score=result.dimension_scores["location"], salary_score=result.dimension_scores["salary"],
        industry_score=result.dimension_scores["industry"], management_score=result.dimension_scores["management"],
        total_score=result.total_score, grade=result.grade.value, eligibility=result.eligibility.value,
        hard_rejected=result.hard_rejected, match_reasons=result.match_reasons,
        risk_notes=result.risk_notes, input_snapshot=snapshot,
        details=[db.JobScoreDetail(dimension=item.dimension, rule_code=item.rule_code,
                                   score_awarded=item.score, max_score=item.max_score,
                                   matched_facts=item.matched_facts, explanation=item.explanation,
                                   sort_order=index) for index, item in enumerate(result.details)],
        rejections=[db.JobRejection(rule_code=item.rule_code, message=item.message,
                                    evidence=item.evidence, sort_order=index)
                    for index, item in enumerate(result.rejection_reasons)],
    )
    session.add(score)
    session.commit()
    session.refresh(score)
    return _response(score)


def get_score(session: Session, score_id: object) -> ScoreResponse:
    score = session.get(db.JobScore, score_id)
    if score is None:
        raise ResourceNotFoundError("评分不存在")
    job = get_job_entity(session, score.job_id)
    if job.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("评分不存在")
    return _response(score)


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
    return [_response(row) for row in rows], total


def _fingerprint(job_id: object, strategy: db.JobStrategy, profile: db.CandidateProfile,
                 parsed: db.ParsedJobDetail) -> str:
    payload = [str(job_id), str(strategy.id), strategy.version, str(profile.id), profile.version,
               str(parsed.id), SCORING_VERSION]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _response(score: db.JobScore) -> ScoreResponse:
    details = [{"dimension": item.dimension, "score": item.score_awarded,
                "max_score": item.max_score, "rule_code": item.rule_code,
                "explanation": item.explanation, "matched_facts": item.matched_facts}
               for item in sorted(score.details, key=lambda item: item.sort_order)]
    rejections = [{"rule_code": item.rule_code, "message": item.message,
                   "evidence": item.evidence}
                  for item in sorted(score.rejections, key=lambda item: item.sort_order)]
    dimensions = {"title": score.title_score, "skills": score.skill_score,
                  "experience": score.experience_score, "location": score.location_score,
                  "salary": score.salary_score, "industry": score.industry_score,
                  "management": score.management_score}
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
        scoring_version=score.scoring_version,
    )
