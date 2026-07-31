"""add action execution evidence and draftless policy decisions

Revision ID: 20260731_0035
Revises: 20260729_0034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0035"
down_revision: str | None = "20260729_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("policy_decisions", "draft_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column(
        "action_queue",
        sa.Column(
            "observation_baseline",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "action_queue",
        sa.Column("write_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "action_attempts",
        sa.Column("write_started", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "action_attempts",
        sa.Column(
            "observation_baseline",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("action_attempts", "observation_baseline")
    op.drop_column("action_attempts", "write_started")
    op.drop_column("action_queue", "write_started_at")
    op.drop_column("action_queue", "observation_baseline")
    op.alter_column("policy_decisions", "draft_id", existing_type=postgresql.UUID(), nullable=False)
