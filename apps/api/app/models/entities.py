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


class KnowledgeItem(TimestampMixin, Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (UniqueConstraint("user_id", "category", "normalized_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(150))
    normalized_key: Mapped[str] = mapped_column(String(150))
    fact: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100))
    allowed_for_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitivity: Mapped[str] = mapped_column(String(20), default="NORMAL")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Resume(TimestampMixin, Base):
    __tablename__ = "resumes"
    __table_args__ = (UniqueConstraint("user_id", "platform", "attachment_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(30))
    attachment_name: Mapped[str] = mapped_column(String(255))
    target_directions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("user_id", "platform", "external_conversation_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(30), default="MOCK")
    external_conversation_id: Mapped[str] = mapped_column(String(200))
    recruiter_name: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(30), default="NEW")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "external_message_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    external_message_id: Mapped[str] = mapped_column(String(200))
    direction: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    intents: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(30), default="RECEIVED")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GeneratedDraft(Base):
    __tablename__ = "generated_drafts"
    __table_args__ = (UniqueConstraint("input_fingerprint"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)
    job_score_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_scores.id", ondelete="CASCADE"), nullable=True)
    draft_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    intents: Mapped[list[str]] = mapped_column(JSONB, default=list)
    fact_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    risk_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    generator_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_drafts.id", ondelete="CASCADE"))
    action_type: Mapped[str] = mapped_column(String(30))
    decision: Mapped[str] = mapped_column(String(30))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    policy_version: Mapped[str] = mapped_column(String(50))
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConfirmationTask(TimestampMixin, Base):
    __tablename__ = "confirmation_tasks"
    __table_args__ = (UniqueConstraint("decision_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_decisions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="PENDING_APPROVAL")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformSession(TimestampMixin, Base):
    __tablename__ = "platform_sessions"
    __table_args__ = (UniqueConstraint("user_id", "platform"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(30))
    cdp_endpoint: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40))
    last_reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrowserReadRun(Base):
    __tablename__ = "browser_read_runs"
    __table_args__ = (UniqueConstraint("input_fingerprint"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform_sessions.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40))
    page_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    imported_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    imported_conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    imported_message_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PageEvidence(Base):
    __tablename__ = "page_evidence"
    __table_args__ = (UniqueConstraint("browser_read_run_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    browser_read_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_read_runs.id", ondelete="CASCADE"))
    page_url: Mapped[str] = mapped_column(Text)
    page_title: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    selector_version: Mapped[str] = mapped_column(String(50))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionQueue(TimestampMixin, Base):
    __tablename__ = "action_queue"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("confirmation_task_id"),
        UniqueConstraint("send_fingerprint"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    confirmation_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("confirmation_tasks.id"), nullable=True)
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_decisions.id"), nullable=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_strategies.id"), nullable=True)
    authorization_source: Mapped[str] = mapped_column(String(20), default="MANUAL")
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    draft_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generated_drafts.id"), nullable=True)
    resume_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str] = mapped_column(String(30))
    target_company: Mapped[str] = mapped_column(String(200))
    target_job_title: Mapped[str] = mapped_column(String(200))
    target_recruiter: Mapped[str] = mapped_column(String(100))
    target_conversation_key: Mapped[str] = mapped_column(String(200))
    attachment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    send_fingerprint: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class AutomationSetting(TimestampMixin, Base):
    __tablename__ = "automation_settings"
    __table_args__ = (UniqueConstraint("user_id", "scope_type", "scope_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_key: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_greet_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_greet_min_score: Mapped[int] = mapped_column(Integer, default=70)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_reply_min_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0.90"))
    auto_resume_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_resume_min_score: Mapped[int] = mapped_column(Integer, default=70)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=10)
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)


class ActionAttempt(Base):
    __tablename__ = "action_attempts"
    __table_args__ = (UniqueConstraint("action_id", "attempt_number"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("action_queue.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResumeSendRecord(Base):
    __tablename__ = "resume_send_records"
    __table_args__ = (UniqueConstraint("conversation_id", "resume_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("action_queue.id"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    actor_type: Mapped[str] = mapped_column(String(30))
    event_type: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    before_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    after_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchedulingPreference(TimestampMixin, Base):
    __tablename__ = "scheduling_preferences"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CalendarEvent(TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("user_id", "provider", "external_event_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(30), default="MOCK")
    external_event_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    availability: Mapped[str] = mapped_column(String(30), default="BUSY")
    source: Mapped[str] = mapped_column(String(30), default="IMPORTED")


class InterviewRequest(TimestampMixin, Base):
    __tablename__ = "interview_requests"
    __table_args__ = (UniqueConstraint("message_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    source_text: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(80))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parse_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    risk_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_APPROVAL")
    candidate_slots: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)


class CalendarCheck(Base):
    __tablename__ = "calendar_checks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interview_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_requests.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30))
    snapshot_version: Mapped[str] = mapped_column(String(64))
    conflicts: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScheduleConfirmation(TimestampMixin, Base):
    __tablename__ = "schedule_confirmations"
    __table_args__ = (UniqueConstraint("interview_request_id"), UniqueConstraint("idempotency_key"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interview_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_requests.id", ondelete="CASCADE"))
    calendar_check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calendar_checks.id"))
    status: Mapped[str] = mapped_column(String(30), default="PENDING_APPROVAL")
    selected_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_content: Mapped[str] = mapped_column(Text)
    create_calendar_event: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("action_queue.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
