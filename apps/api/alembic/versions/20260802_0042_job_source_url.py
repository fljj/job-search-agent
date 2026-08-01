"""persist canonical job source urls

Revision ID: 20260802_0042
Revises: 20260801_0041
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0042"
down_revision: str | None = "20260801_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("source_url", sa.String(length=2000), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("source_url_observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "source_url_observed_at")
    op.drop_column("jobs", "source_url")
