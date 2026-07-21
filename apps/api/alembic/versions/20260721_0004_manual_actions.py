"""创建第四阶段人工确认动作、执行尝试和审计表。

Revision ID: 20260721_0004
Revises: 20260721_0003
"""
from alembic import op

from apps.api.app.core.database import Base
from apps.api.app.models import entities  # noqa: F401

revision = "20260721_0004"
down_revision = "20260721_0003"
branch_labels = None
depends_on = None

TABLE_NAMES = ("action_queue", "action_attempts", "resume_send_records", "audit_events")


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
