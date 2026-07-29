"""增加兼职求职策略。

Revision ID: 20260729_0033
Revises: 20260729_0032
"""

import sqlalchemy as sa
from alembic import op

revision = "20260729_0033"
down_revision = "20260729_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_strategies",
        sa.Column(
            "accept_part_time",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("job_strategies", "accept_part_time")
