import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(100))


class CandidateProfile(TimestampMixin, Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    total_years: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=0)
    management_years: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=0)
    has_architecture_experience: Mapped[bool] = mapped_column(Boolean, default=False)
    has_core_system_experience: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    skills: Mapped[list["CandidateSkill"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    industries: Mapped[list["CandidateIndustryExperience"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class CandidateSkill(TimestampMixin, Base):
    __tablename__ = "candidate_skills"
    __table_args__ = (UniqueConstraint("candidate_profile_id", "normalized_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100))
    years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    proficiency: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str] = mapped_column(String(100))
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)


class CandidateIndustryExperience(Base):
    __tablename__ = "candidate_industry_experiences"
    __table_args__ = (UniqueConstraint("candidate_profile_id", "industry_code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    industry_code: Mapped[str] = mapped_column(String(100))
    years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    source: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobStrategy(TimestampMixin, Base):
    __tablename__ = "job_strategies"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id"))
    name: Mapped[str] = mapped_column(String(150))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    accepted_seniority_levels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    max_posted_days: Mapped[int] = mapped_column(Integer, default=30)
    accept_outsourcing: Mapped[bool] = mapped_column(Boolean, default=False)
    accept_headhunter: Mapped[bool] = mapped_column(Boolean, default=True)
    core_required_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title_rules: Mapped[list["JobTitleRule"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    work_mode_rules: Mapped[list["WorkModeRule"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    salary_rules: Mapped[list["SalaryRule"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    industry_rules: Mapped[list["IndustryRule"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    blacklist: Mapped[list["CompanyBlacklist"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class JobTitleRule(Base):
    __tablename__ = "job_title_rules"
    __table_args__ = (UniqueConstraint("strategy_id", "rule_type", "normalized_pattern"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_strategies.id", ondelete="CASCADE"))
    rule_type: Mapped[str] = mapped_column(String(20))
    pattern: Mapped[str] = mapped_column(String(150))
    normalized_pattern: Mapped[str] = mapped_column(String(150))
    score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=15)
    is_hard_requirement: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkModeRule(TimestampMixin, Base):
    __tablename__ = "work_mode_rules"
    __table_args__ = (UniqueConstraint("strategy_id", "work_mode"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_strategies.id", ondelete="CASCADE"))
    work_mode: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    location_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    location_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=15)
    unknown_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=8)
    locations: Mapped[list["WorkModeLocation"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class WorkModeLocation(Base):
    __tablename__ = "work_mode_locations"
    __table_args__ = (UniqueConstraint("work_mode_rule_id", "location_code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_mode_rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_mode_rules.id", ondelete="CASCADE"))
    location_code: Mapped[str] = mapped_column(String(100))
    location_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SalaryRule(TimestampMixin, Base):
    __tablename__ = "salary_rules"
    __table_args__ = (UniqueConstraint("strategy_id", "work_mode", "currency"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_strategies.id", ondelete="CASCADE"))
    work_mode: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    minimum_monthly_k: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    expected_monthly_k: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    negotiable_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=8)
    unknown_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=8)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    exchange_rate_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bands: Mapped[list["SalaryScoreBand"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class SalaryScoreBand(Base):
    __tablename__ = "salary_score_bands"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    salary_rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("salary_rules.id", ondelete="CASCADE"))
    lower_bound_k: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    upper_bound_k: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    min_score: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    max_score: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    interpolation: Mapped[str] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer)


class IndustryRule(Base):
    __tablename__ = "industry_rules"
    __table_args__ = (UniqueConstraint("strategy_id", "industry_code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_strategies.id", ondelete="CASCADE"))
    industry_code: Mapped[str] = mapped_column(String(100))
    industry_name: Mapped[str] = mapped_column(String(100))
    rule_type: Mapped[str] = mapped_column(String(20))
    score: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyBlacklist(Base):
    __tablename__ = "company_blacklists"
    __table_args__ = (UniqueConstraint("strategy_id", "normalized_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_strategies.id", ondelete="CASCADE"))
    company_name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("uq_jobs_external", "user_id", "source", "external_job_id", unique=True,
              postgresql_where=text("external_job_id IS NOT NULL")),
        UniqueConstraint("user_id", "source", "content_hash"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(30), default="MOCK")
    external_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    company_name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(150), nullable=True)
    location: Mapped[str | None] = mapped_column(String(150), nullable=True)
    work_mode: Mapped[str] = mapped_column(String(20))
    salary_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_status: Mapped[str] = mapped_column(String(20))
    raw_data: Mapped[dict[str, object]] = mapped_column(JSONB)


class ParsedJobDetail(Base):
    __tablename__ = "parsed_job_details"
    __table_args__ = (Index("ix_parsed_job_created", "job_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    parser_type: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(50))
    required_skills: Mapped[list[str]] = mapped_column(JSONB)
    preferred_skills: Mapped[list[str]] = mapped_column(JSONB)
    years_required: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    management_required: Mapped[bool] = mapped_column(Boolean)
    architecture_required: Mapped[bool] = mapped_column(Boolean)
    seniority_level: Mapped[str] = mapped_column(String(20))
    responsibilities: Mapped[list[str]] = mapped_column(JSONB)
    salary_normalized: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    flags: Mapped[dict[str, bool]] = mapped_column(JSONB)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    warnings: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobScore(Base):
    __tablename__ = "job_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_strategies.id", ondelete="CASCADE"))
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id"))
    parsed_job_detail_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parsed_job_details.id"))
    strategy_version: Mapped[int] = mapped_column(Integer)
    profile_version: Mapped[int] = mapped_column(Integer)
    scoring_version: Mapped[str] = mapped_column(String(50))
    input_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    effective_job_status: Mapped[str] = mapped_column(String(20))
    action_blockers: Mapped[list[str]] = mapped_column(JSONB)
    title_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    skill_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    experience_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    location_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    salary_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    industry_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    management_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    total_score: Mapped[int] = mapped_column(Integer)
    grade: Mapped[str] = mapped_column(String(1))
    eligibility: Mapped[str] = mapped_column(String(20))
    hard_rejected: Mapped[bool] = mapped_column(Boolean)
    match_reasons: Mapped[list[str]] = mapped_column(JSONB)
    risk_notes: Mapped[list[str]] = mapped_column(JSONB)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    details: Mapped[list["JobScoreDetail"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    rejections: Mapped[list["JobRejection"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class JobScoreDetail(Base):
    __tablename__ = "job_score_details"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_score_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_scores.id", ondelete="CASCADE"))
    dimension: Mapped[str] = mapped_column(String(30))
    rule_code: Mapped[str] = mapped_column(String(100))
    score_awarded: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    max_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    matched_facts: Mapped[dict[str, object]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)


class JobRejection(Base):
    __tablename__ = "job_rejections"
    __table_args__ = (UniqueConstraint("job_score_id", "rule_code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_score_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_scores.id", ondelete="CASCADE"))
    rule_code: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB)
    sort_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
