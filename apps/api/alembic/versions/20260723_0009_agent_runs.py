"""增加 Agent 运行、租约、事件与动作追溯。

Revision ID: 20260723_0009
Revises: 20260723_0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_0009"
down_revision = "20260723_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column(
            "strategy_id",
            postgresql.UUID(),
            sa.ForeignKey("job_strategies.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="RUNNING"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "pause_reason_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_agent_runs_version"),
    )
    op.create_index(
        "uq_agent_runs_active_user_platform",
        "agent_runs",
        ["user_id", "platform"],
        unique=True,
        postgresql_where=sa.text("status IN ('RUNNING', 'PAUSED')"),
    )
    op.create_table(
        "agent_run_events",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=True),
        sa.Column("entity_id", postgresql.UUID(), nullable=True),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("action_queue", sa.Column("agent_run_id", postgresql.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_action_queue_agent_run",
        "action_queue",
        "agent_runs",
        ["agent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_action_queue_agent_run", "action_queue", type_="foreignkey")
    op.drop_column("action_queue", "agent_run_id")
    op.drop_table("agent_run_events")
    op.drop_index("uq_agent_runs_active_user_platform", table_name="agent_runs")
    op.drop_table("agent_runs")
