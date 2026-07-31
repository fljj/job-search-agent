"""remove unused automation setting fields

Revision ID: 20260731_0039
Revises: 20260731_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0039"
down_revision: str | None = "20260731_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_automation_resume_min_score", "automation_settings", type_="check"
    )
    op.drop_column("automation_settings", "auto_resume_min_score")
    op.drop_column("automation_settings", "auto_reply_min_confidence")
    op.drop_column("automation_settings", "low_score_decline_enabled")


def downgrade() -> None:
    op.add_column(
        "automation_settings",
        sa.Column(
            "low_score_decline_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "auto_reply_min_confidence",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="0.90",
        ),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "auto_resume_min_score",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    op.create_check_constraint(
        "ck_automation_resume_min_score",
        "automation_settings",
        "auto_resume_min_score BETWEEN 60 AND 100",
    )
