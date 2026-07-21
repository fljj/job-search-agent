"""创建第三阶段只读浏览器会话和证据表。

Revision ID: 20260721_0003
Revises: 20260721_0002
"""
from alembic import op

from apps.api.app.core.database import Base
from apps.api.app.models import entities  # noqa: F401

revision = "20260721_0003"
down_revision = "20260721_0002"
branch_labels = None
depends_on = None

TABLE_NAMES = ("platform_sessions", "browser_read_runs", "page_evidence")


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
