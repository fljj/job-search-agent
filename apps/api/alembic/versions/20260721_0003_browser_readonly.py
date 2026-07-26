"""创建第三阶段只读浏览器会话和证据表。

Revision ID: 20260721_0003
Revises: 20260721_0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0003"
down_revision = "20260721_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("cdp_endpoint", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("last_reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", "platform"),
    )
    op.create_table(
        "browser_read_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("platform_session_id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("page_type", sa.String(length=30), nullable=True),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("imported_job_id", sa.UUID(), nullable=True),
        sa.Column("imported_conversation_id", sa.UUID(), nullable=True),
        sa.Column("imported_message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["imported_conversation_id"], ["conversations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["imported_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["platform_session_id"], ["platform_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_fingerprint"),
    )
    op.create_table(
        "page_evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("browser_read_run_id", sa.UUID(), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("page_title", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("selector_version", sa.String(length=50), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["browser_read_run_id"], ["browser_read_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("browser_read_run_id"),
    )


def downgrade() -> None:
    op.drop_table("page_evidence")
    op.drop_table("browser_read_runs")
    op.drop_table("platform_sessions")
