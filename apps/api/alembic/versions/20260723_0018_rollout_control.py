"""增加无人值守灰度控制。

Revision ID: 20260723_0018
Revises: 20260723_0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_0018"
down_revision = "20260723_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rollout_controls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PAUSED"),
        sa.Column("current_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("previous_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("stage_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minimum_stage_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("reply_daily_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("greeting_daily_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "platform"),
        sa.CheckConstraint("current_level BETWEEN 1 AND 6", name="ck_rollout_level"),
        sa.CheckConstraint(
            "previous_level BETWEEN 1 AND 6", name="ck_rollout_previous_level"
        ),
        sa.CheckConstraint(
            "minimum_stage_hours >= 24", name="ck_rollout_minimum_hours"
        ),
        sa.CheckConstraint(
            "reply_daily_limit BETWEEN 1 AND 5", name="ck_rollout_reply_limit"
        ),
        sa.CheckConstraint(
            "greeting_daily_limit BETWEEN 1 AND 3", name="ck_rollout_greeting_limit"
        ),
    )


def downgrade() -> None:
    op.drop_table("rollout_controls")
