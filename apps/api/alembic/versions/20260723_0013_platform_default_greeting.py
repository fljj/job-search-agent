"""记录平台默认招呼的预期与实际内容。

Revision ID: 20260723_0013
Revises: 20260723_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_0013"
down_revision = "20260723_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_queue",
        sa.Column(
            "delivery_mode",
            sa.String(length=30),
            nullable=False,
            server_default="CUSTOM",
        ),
    )
    op.add_column(
        "action_queue",
        sa.Column("expected_platform_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "action_queue",
        sa.Column("observed_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "action_attempts",
        sa.Column("observed_content", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("action_attempts", "observed_content")
    op.drop_column("action_queue", "observed_content")
    op.drop_column("action_queue", "expected_platform_content")
    op.drop_column("action_queue", "delivery_mode")
