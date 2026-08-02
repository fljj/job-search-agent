"""将混合办公职位归一为现场办公。

Revision ID: 20260802_0047
Revises: 20260802_0046
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0047"
down_revision: str | None = "20260802_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE jobs SET work_mode = 'ONSITE' WHERE work_mode = 'HYBRID'")


def downgrade() -> None:
    # ONSITE 中无法区分迁移前的混合办公职位。
    pass
