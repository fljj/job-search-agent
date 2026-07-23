"""增加猎头岗位分数封顶策略。

Revision ID: 20260723_0011
Revises: 20260723_0010
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_0011"
down_revision = "20260723_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("job_strategies")
    }
    checks = {
        check["name"]
        for check in inspector.get_check_constraints("job_strategies")
    }
    if "headhunter_score_cap" not in columns:
        op.add_column(
            "job_strategies",
            sa.Column("headhunter_score_cap", sa.Integer(), nullable=True),
        )
    if "ck_job_strategies_headhunter_score_cap" not in checks:
        op.create_check_constraint(
            "ck_job_strategies_headhunter_score_cap",
            "job_strategies",
            "headhunter_score_cap IS NULL OR "
            "(headhunter_score_cap >= 0 AND headhunter_score_cap <= 79)",
        )


def downgrade() -> None:
    op.drop_constraint(
        "ck_job_strategies_headhunter_score_cap",
        "job_strategies",
        type_="check",
    )
    op.drop_column("job_strategies", "headhunter_score_cap")
