"""增加低分婉拒独立自动化开关。

Revision ID: 20260723_0008
Revises: 20260723_0007
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_0008"
down_revision = "20260723_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("automation_settings")
    }
    if "low_score_decline_enabled" not in columns:
        op.add_column(
            "automation_settings",
            sa.Column(
                "low_score_decline_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("automation_settings")
    }
    if "low_score_decline_enabled" in columns:
        op.drop_column("automation_settings", "low_score_decline_enabled")
