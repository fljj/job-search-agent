"""创建第一阶段数据表。

Revision ID: 20260721_0001
Revises: None
"""
from alembic import op

from apps.api.app.core.database import Base
from apps.api.app.models import entities  # noqa: F401

revision = "20260721_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind=op.get_bind(), checkfirst=True)
