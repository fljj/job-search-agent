"""add hot-reloadable llm request timeout

Revision ID: 20260802_0043
Revises: 20260802_0042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0043"
down_revision: str | None = "20260802_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_runtime_settings",
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
    )
    op.create_check_constraint(
        "ck_llm_runtime_settings_timeout_seconds",
        "llm_runtime_settings",
        "timeout_seconds BETWEEN 1 AND 300",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_llm_runtime_settings_timeout_seconds",
        "llm_runtime_settings",
        type_="check",
    )
    op.drop_column("llm_runtime_settings", "timeout_seconds")
