from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.strategy import StrategyPayload, StrategyResponse
from apps.api.app.services.errors import ResourceNotFoundError, VersionConflictError
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.job_parser.normalizers import normalize_company, normalize_location, normalize_text
from packages.scoring.models import (
    IndustryRule,
    SalaryBand,
    SalaryRule,
    Strategy,
    TitleRule,
    WorkModeRule,
)


def create_strategy(session: Session, payload: StrategyPayload) -> StrategyResponse:
    ensure_default_user(session)
    strategy = db.JobStrategy(user_id=DEFAULT_USER_ID, candidate_profile_id=payload.candidate_profile_id,
                              name=payload.name)
    _apply(strategy, payload)
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return _response(strategy)


def list_strategies(session: Session, enabled: bool | None, page: int, page_size: int) -> tuple[list[StrategyResponse], int]:
    query = select(db.JobStrategy).where(db.JobStrategy.user_id == DEFAULT_USER_ID)
    if enabled is not None:
        query = query.where(db.JobStrategy.enabled == enabled)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(db.JobStrategy.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return [_response(item) for item in rows], total


def get_strategy(session: Session, strategy_id: object) -> StrategyResponse:
    strategy = session.get(db.JobStrategy, strategy_id)
    if strategy is None or strategy.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("策略不存在")
    return _response(strategy)


def update_strategy(session: Session, strategy_id: object, payload: StrategyPayload) -> StrategyResponse:
    strategy = session.get(db.JobStrategy, strategy_id)
    if strategy is None or strategy.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("策略不存在")
    if payload.version != strategy.version:
        raise VersionConflictError("策略版本已变化")
    strategy.version += 1
    strategy.title_rules.clear()
    strategy.work_mode_rules.clear()
    strategy.salary_rules.clear()
    strategy.industry_rules.clear()
    strategy.blacklist.clear()
    session.flush()
    _apply(strategy, payload)
    session.commit()
    session.refresh(strategy)
    return _response(strategy)


def set_status(session: Session, strategy_id: object, enabled: bool, version: int) -> StrategyResponse:
    strategy = session.get(db.JobStrategy, strategy_id)
    if strategy is None or strategy.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("策略不存在")
    if version != strategy.version:
        raise VersionConflictError("策略版本已变化")
    strategy.enabled = enabled
    strategy.version += 1
    session.commit()
    session.refresh(strategy)
    return _response(strategy)


def to_domain(strategy: db.JobStrategy) -> Strategy:
    return Strategy.model_validate(_response(strategy).model_dump(exclude={"id", "candidate_profile_id"}))


def _apply(strategy: db.JobStrategy, payload: StrategyPayload) -> None:
    strategy.name = payload.name
    strategy.enabled = payload.enabled
    strategy.priority = payload.priority
    strategy.candidate_profile_id = payload.candidate_profile_id
    strategy.accepted_seniority_levels = [item.value for item in payload.accepted_seniority_levels]
    strategy.max_posted_days = payload.max_posted_days
    strategy.accept_outsourcing = payload.accept_outsourcing
    strategy.accept_part_time = payload.accept_part_time
    strategy.accept_headhunter = payload.accept_headhunter
    strategy.headhunter_score_cap = payload.headhunter_score_cap
    strategy.core_required_skills = payload.core_required_skills
    strategy.arrival_time_reply = payload.arrival_time_reply
    strategy.reject_full_time_bachelor_required = (
        payload.reject_full_time_bachelor_required
    )
    strategy.title_rules = [db.JobTitleRule(rule_type=item.rule_type.value, pattern=item.pattern,
                                            normalized_pattern=normalize_text(item.pattern), score=item.score,
                                            is_hard_requirement=item.is_hard_requirement)
                            for item in payload.title_rules]
    strategy.work_mode_rules = [db.WorkModeRule(
        work_mode=item.work_mode.value, enabled=item.enabled,
        location_restricted=item.location_restricted, location_score=item.score,
        unknown_score=item.unknown_score,
        locations=[db.WorkModeLocation(location_code=normalize_location(location) or location,
                                       location_name=location) for location in item.allowed_locations],
    ) for item in payload.work_mode_rules]
    strategy.salary_rules = [db.SalaryRule(
        work_mode=item.work_mode.value, currency=item.currency,
        minimum_monthly_k=item.minimum_monthly_k, expected_monthly_k=item.expected_monthly_k,
        negotiable_score=item.negotiable_score, unknown_score=item.unknown_score,
        exchange_rate=item.exchange_rate, exchange_rate_version=item.exchange_rate_version,
        bands=[db.SalaryScoreBand(lower_bound_k=band.lower_bound_k,
                                  upper_bound_k=band.upper_bound_k, min_score=band.min_score,
                                  max_score=band.max_score, interpolation=band.interpolation.value,
                                  sort_order=index) for index, band in enumerate(item.bands)],
    ) for item in payload.salary_rules]
    strategy.industry_rules = [db.IndustryRule(industry_code=normalize_text(item.industry),
                                               industry_name=item.industry,
                                               rule_type=item.rule_type.value, score=item.score)
                               for item in payload.industry_rules]
    strategy.blacklist = [db.CompanyBlacklist(company_name=item,
                                               normalized_name=normalize_company(item))
                          for item in payload.company_blacklist]


def _response(strategy: db.JobStrategy) -> StrategyResponse:
    return StrategyResponse(
        id=strategy.id, candidate_profile_id=strategy.candidate_profile_id, name=strategy.name,
        enabled=strategy.enabled, priority=strategy.priority, version=strategy.version,
        accepted_seniority_levels=strategy.accepted_seniority_levels,
        max_posted_days=strategy.max_posted_days,
        accept_outsourcing=strategy.accept_outsourcing,
        accept_part_time=strategy.accept_part_time,
        accept_headhunter=strategy.accept_headhunter,
        headhunter_score_cap=strategy.headhunter_score_cap,
        core_required_skills=strategy.core_required_skills,
        arrival_time_reply=strategy.arrival_time_reply,
        reject_full_time_bachelor_required=(
            strategy.reject_full_time_bachelor_required
        ),
        title_rules=[TitleRule(rule_type=item.rule_type, pattern=item.pattern, score=item.score,
                               is_hard_requirement=item.is_hard_requirement) for item in strategy.title_rules],
        work_mode_rules=[WorkModeRule(work_mode=item.work_mode, enabled=item.enabled,
                                      allowed_locations=[location.location_name for location in item.locations],
                                      location_restricted=item.location_restricted,
                                      score=item.location_score, unknown_score=item.unknown_score)
                         for item in strategy.work_mode_rules],
        salary_rules=[SalaryRule(
            work_mode=item.work_mode, currency=item.currency,
            minimum_monthly_k=item.minimum_monthly_k, expected_monthly_k=item.expected_monthly_k,
            negotiable_score=item.negotiable_score, unknown_score=item.unknown_score,
            exchange_rate=item.exchange_rate, exchange_rate_version=item.exchange_rate_version,
            bands=[SalaryBand(lower_bound_k=band.lower_bound_k, upper_bound_k=band.upper_bound_k,
                              min_score=band.min_score, max_score=band.max_score,
                              interpolation=band.interpolation) for band in sorted(item.bands, key=lambda band: band.sort_order)],
        ) for item in strategy.salary_rules],
        industry_rules=[IndustryRule(industry=item.industry_name, rule_type=item.rule_type,
                                     score=item.score) for item in strategy.industry_rules],
        company_blacklist=[item.company_name for item in strategy.blacklist],
    )
