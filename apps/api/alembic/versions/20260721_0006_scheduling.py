"""增加第六阶段排期、日历检查和时间确认表。

Revision ID: 20260721_0006
Revises: 20260721_0005
"""
from alembic import op

from apps.api.app.core.database import Base
from apps.api.app.models import entities  # noqa: F401

revision = "20260721_0006"
down_revision = "20260721_0005"
branch_labels = None
depends_on = None

TABLE_NAMES = (
    "scheduling_preferences", "calendar_events", "interview_requests",
    "calendar_checks", "schedule_confirmations",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
