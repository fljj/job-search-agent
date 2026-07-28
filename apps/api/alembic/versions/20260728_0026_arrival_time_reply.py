"""增加求职策略公开到岗口径。

Revision ID: 20260728_0026
Revises: 20260726_0025
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_0026"
down_revision = "20260726_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_strategies",
        sa.Column("arrival_time_reply", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_strategies", "arrival_time_reply")
