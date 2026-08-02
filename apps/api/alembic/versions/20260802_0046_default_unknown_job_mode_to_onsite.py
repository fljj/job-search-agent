"""将未知职位工作模式归一为现场办公。

Revision ID: 20260802_0046
Revises: 20260802_0045
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0046"
down_revision: str | None = "20260802_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE jobs SET work_mode = 'ONSITE' WHERE work_mode = 'UNKNOWN'")


def downgrade() -> None:
    # ONSITE 中无法区分迁移前的明确现场职位和原 UNKNOWN 职位。
    pass
