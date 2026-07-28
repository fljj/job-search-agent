"""增加职位发现单飞重试和退避字段。

Revision ID: 20260728_0028
Revises: 20260728_0027
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_0028"
down_revision = "20260728_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_discovery_records",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "job_discovery_records",
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_job_discovery_records_retry_count_nonnegative",
        "job_discovery_records",
        "retry_count >= 0",
    )
    op.create_index(
        "ix_job_discovery_records_retry_due",
        "job_discovery_records",
        ["agent_run_id", "status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_discovery_records_retry_due",
        table_name="job_discovery_records",
    )
    op.drop_constraint(
        "ck_job_discovery_records_retry_count_nonnegative",
        "job_discovery_records",
        type_="check",
    )
    op.drop_column("job_discovery_records", "next_retry_at")
    op.drop_column("job_discovery_records", "retry_count")
