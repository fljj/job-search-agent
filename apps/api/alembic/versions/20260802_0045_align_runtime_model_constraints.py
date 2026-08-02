"""对齐运行时模型约束。

Revision ID: 20260802_0045
Revises: 20260802_0044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0045"
down_revision: str | None = "20260802_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE llm_runtime_settings
        SET created_at = COALESCE(created_at, now()),
            updated_at = COALESCE(updated_at, created_at, now())
        WHERE created_at IS NULL OR updated_at IS NULL
        """
    )
    op.alter_column(
        "llm_runtime_settings",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "llm_runtime_settings",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "llm_runtime_settings",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "llm_runtime_settings",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
