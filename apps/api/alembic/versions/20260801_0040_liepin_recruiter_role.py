"""add structured recruiter role to jobs

Revision ID: 20260801_0040
Revises: 20260731_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0040"
down_revision: str | None = "20260731_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "recruiter_role",
            sa.String(30),
            server_default="UNKNOWN",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "recruiter_role")
