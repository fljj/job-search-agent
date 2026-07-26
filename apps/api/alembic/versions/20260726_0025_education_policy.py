"""增加本科培养形式和全日制本科硬性规则配置。

Revision ID: 20260726_0025
Revises: 20260726_0024
"""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0025"
down_revision = "20260726_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("bachelor_full_time", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "job_strategies",
        sa.Column(
            "reject_full_time_bachelor_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("job_strategies", "reject_full_time_bachelor_required")
    op.drop_column("candidate_profiles", "bachelor_full_time")
