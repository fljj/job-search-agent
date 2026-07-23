"""允许入站对话在尚未绑定职位时保存。

Revision ID: 20260724_0022
Revises: 20260724_0021
"""

from alembic import op

revision = "20260724_0022"
down_revision = "20260724_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("conversations", "job_id", nullable=True)


def downgrade() -> None:
    op.alter_column("conversations", "job_id", nullable=False)
