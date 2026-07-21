"""创建第二阶段知识库和对话草稿表。

Revision ID: 20260721_0002
Revises: 20260721_0001
"""
from alembic import op

from apps.api.app.core.database import Base
from apps.api.app.models import entities  # noqa: F401

revision = "20260721_0002"
down_revision = "20260721_0001"
branch_labels = None
depends_on = None

TABLE_NAMES = (
    "knowledge_items", "resumes", "conversations", "messages",
    "generated_drafts", "policy_decisions", "confirmation_tasks",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
