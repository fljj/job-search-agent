"""增加职位发现记录与扫描安全配置。

Revision ID: 20260723_0016
Revises: 20260723_0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_0016"
down_revision = "20260723_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [
        sa.Column("emergency_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("job_scan_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hourly_scan_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("daily_scan_limit", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("company_cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("recruiter_cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("work_start_hour", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("work_end_hour", sa.Integer(), nullable=False, server_default="22"),
    ]
    for column in columns:
        op.add_column("automation_settings", column)
    op.create_table(
        "job_discovery_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_job_id", sa.String(200), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("job_title", sa.String(200), nullable=False),
        sa.Column("recruiter_name", sa.String(100), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_score_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_scores.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_queue.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("agent_run_id", "external_job_id"),
    )


def downgrade() -> None:
    op.drop_table("job_discovery_records")
    for name in [
        "work_end_hour",
        "work_start_hour",
        "recruiter_cooldown_hours",
        "company_cooldown_hours",
        "daily_scan_limit",
        "hourly_scan_limit",
        "job_scan_enabled",
        "emergency_stop",
    ]:
        op.drop_column("automation_settings", name)
