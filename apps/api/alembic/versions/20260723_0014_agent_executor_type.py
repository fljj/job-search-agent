"""记录 Agent 运行使用的执行器类型。

Revision ID: 20260723_0014
Revises: 20260723_0013
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_0014"
down_revision = "20260723_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "executor_type",
            sa.String(length=20),
            nullable=False,
            server_default="UNASSIGNED",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "executor_type")
