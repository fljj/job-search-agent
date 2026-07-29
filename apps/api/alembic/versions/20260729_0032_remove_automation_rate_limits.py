"""删除自动动作与职位扫描的小时/每日配额。

Revision ID: 20260729_0032
Revises: 20260729_0031
"""

import sqlalchemy as sa
from alembic import op

revision = "20260729_0032"
down_revision = "20260729_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("automation_settings", "daily_scan_limit")
    op.drop_column("automation_settings", "hourly_scan_limit")
    op.drop_column("automation_settings", "daily_limit")
    op.drop_column("automation_settings", "hourly_limit")


def downgrade() -> None:
    op.add_column(
        "automation_settings",
        sa.Column("hourly_limit", sa.Integer(), server_default="10", nullable=False),
    )
    op.add_column(
        "automation_settings",
        sa.Column("daily_limit", sa.Integer(), server_default="50", nullable=False),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "hourly_scan_limit",
            sa.Integer(),
            server_default="100",
            nullable=False,
        ),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "daily_scan_limit",
            sa.Integer(),
            server_default="500",
            nullable=False,
        ),
    )
