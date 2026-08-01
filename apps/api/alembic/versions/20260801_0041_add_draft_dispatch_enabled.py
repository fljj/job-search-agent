"""add generated draft dispatch guard

Revision ID: 20260801_0041
Revises: 20260801_0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0041"
down_revision: str | None = "20260801_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_drafts",
        sa.Column(
            "dispatch_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("generated_drafts", "dispatch_enabled")
