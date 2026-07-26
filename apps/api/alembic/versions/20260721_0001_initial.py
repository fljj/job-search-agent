"""创建第一阶段数据表。

Revision ID: 20260721_0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("total_years", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("management_years", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("has_architecture_experience", sa.Boolean(), nullable=False),
        sa.Column("has_core_system_experience", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "candidate_skills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("candidate_profile_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("years", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("proficiency", sa.String(length=30), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("is_core", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"], ["candidate_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_profile_id", "normalized_name"),
    )
    op.create_table(
        "candidate_industry_experiences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("candidate_profile_id", sa.UUID(), nullable=False),
        sa.Column("industry_code", sa.String(length=100), nullable=False),
        sa.Column("years", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"], ["candidate_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_profile_id", "industry_code"),
    )
    op.create_table(
        "job_strategies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("candidate_profile_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "accepted_seniority_levels", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("max_posted_days", sa.Integer(), nullable=False),
        sa.Column("accept_outsourcing", sa.Boolean(), nullable=False),
        sa.Column("accept_headhunter", sa.Boolean(), nullable=False),
        sa.Column("core_required_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"],
            ["candidate_profiles.id"],
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_table(
        "job_title_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("rule_type", sa.String(length=20), nullable=False),
        sa.Column("pattern", sa.String(length=150), nullable=False),
        sa.Column("normalized_pattern", sa.String(length=150), nullable=False),
        sa.Column("score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("is_hard_requirement", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["job_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "rule_type", "normalized_pattern"),
    )
    op.create_table(
        "work_mode_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("work_mode", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("location_restricted", sa.Boolean(), nullable=False),
        sa.Column("location_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("unknown_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["job_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "work_mode"),
    )
    op.create_table(
        "work_mode_locations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("work_mode_rule_id", sa.UUID(), nullable=False),
        sa.Column("location_code", sa.String(length=100), nullable=False),
        sa.Column("location_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["work_mode_rule_id"], ["work_mode_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_mode_rule_id", "location_code"),
    )
    op.create_table(
        "salary_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("work_mode", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("minimum_monthly_k", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("expected_monthly_k", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("negotiable_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("unknown_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("exchange_rate_version", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["job_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "work_mode", "currency"),
    )
    op.create_table(
        "salary_score_bands",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("salary_rule_id", sa.UUID(), nullable=False),
        sa.Column("lower_bound_k", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("upper_bound_k", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("min_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("max_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("interpolation", sa.String(length=20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["salary_rule_id"], ["salary_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "industry_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("industry_code", sa.String(length=100), nullable=False),
        sa.Column("industry_name", sa.String(length=100), nullable=False),
        sa.Column("rule_type", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["job_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "industry_code"),
    )
    op.create_table(
        "company_blacklists",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["job_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "normalized_name"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("external_job_id", sa.String(length=200), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("industry", sa.String(length=150), nullable=True),
        sa.Column("location", sa.String(length=150), nullable=True),
        sa.Column("work_mode", sa.String(length=20), nullable=False),
        sa.Column("salary_text", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_status", sa.String(length=20), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source", "content_hash"),
    )
    op.create_index(
        "uq_jobs_external",
        "jobs",
        ["user_id", "source", "external_job_id"],
        unique=True,
        postgresql_where=sa.text("external_job_id IS NOT NULL"),
    )

    op.create_table(
        "parsed_job_details",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("parser_type", sa.String(length=30), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("required_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preferred_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("years_required", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("management_required", sa.Boolean(), nullable=False),
        sa.Column("architecture_required", sa.Boolean(), nullable=False),
        sa.Column("seniority_level", sa.String(length=20), nullable=False),
        sa.Column("responsibilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("salary_normalized", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_parsed_job_created", "parsed_job_details", ["job_id", "created_at"], unique=False
    )

    op.create_table(
        "job_scores",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("candidate_profile_id", sa.UUID(), nullable=False),
        sa.Column("parsed_job_detail_id", sa.UUID(), nullable=False),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("scoring_version", sa.String(length=50), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("effective_job_status", sa.String(length=20), nullable=False),
        sa.Column("action_blockers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("skill_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("experience_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("location_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("salary_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("industry_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("management_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("grade", sa.String(length=1), nullable=False),
        sa.Column("eligibility", sa.String(length=20), nullable=False),
        sa.Column("hard_rejected", sa.Boolean(), nullable=False),
        sa.Column("match_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"],
            ["candidate_profiles.id"],
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parsed_job_detail_id"],
            ["parsed_job_details.id"],
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["job_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_fingerprint"),
    )
    op.create_table(
        "job_score_details",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_score_id", sa.UUID(), nullable=False),
        sa.Column("dimension", sa.String(length=30), nullable=False),
        sa.Column("rule_code", sa.String(length=100), nullable=False),
        sa.Column("score_awarded", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("max_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("matched_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["job_score_id"], ["job_scores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "job_rejections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_score_id", sa.UUID(), nullable=False),
        sa.Column("rule_code", sa.String(length=100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_score_id"], ["job_scores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_score_id", "rule_code"),
    )


def downgrade() -> None:
    op.drop_table("job_rejections")
    op.drop_table("job_score_details")
    op.drop_table("job_scores")
    op.drop_table("parsed_job_details")
    op.drop_table("jobs")
    op.drop_table("company_blacklists")
    op.drop_table("industry_rules")
    op.drop_table("salary_score_bands")
    op.drop_table("salary_rules")
    op.drop_table("work_mode_locations")
    op.drop_table("work_mode_rules")
    op.drop_table("job_title_rules")
    op.drop_table("job_strategies")
    op.drop_table("candidate_industry_experiences")
    op.drop_table("candidate_skills")
    op.drop_table("candidate_profiles")
    op.drop_table("users")
