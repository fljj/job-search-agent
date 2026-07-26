"""增加第六阶段排期、日历检查和时间确认表。

Revision ID: 20260721_0006
Revises: 20260721_0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0006"
down_revision = "20260721_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduling_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        "calendar_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("external_event_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
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
        sa.UniqueConstraint("user_id", "provider", "external_event_id"),
    )
    op.create_table(
        "interview_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("parse_confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("risk_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("candidate_slots", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_table(
        "calendar_checks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("interview_request_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("snapshot_version", sa.String(length=64), nullable=False),
        sa.Column("conflicts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["interview_request_id"], ["interview_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "schedule_confirmations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("interview_request_id", sa.UUID(), nullable=False),
        sa.Column("calendar_check_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("selected_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_content", sa.Text(), nullable=False),
        sa.Column("create_calendar_event", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("action_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            ["action_id"],
            ["action_queue.id"],
        ),
        sa.ForeignKeyConstraint(
            ["calendar_check_id"],
            ["calendar_checks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["interview_request_id"], ["interview_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("interview_request_id"),
    )


def downgrade() -> None:
    op.drop_table("schedule_confirmations")
    op.drop_table("calendar_checks")
    op.drop_table("interview_requests")
    op.drop_table("calendar_events")
    op.drop_table("scheduling_preferences")
