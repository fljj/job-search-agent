"""支持职位详情页首次招呼动作。

Revision ID: 20260723_0012
Revises: 20260723_0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_0012"
down_revision = "20260723_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_queue",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_action_queue_job_id_jobs",
        "action_queue",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("action_queue", "conversation_id", nullable=True)
    op.alter_column("action_queue", "target_conversation_key", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM action_queue WHERE conversation_id IS NULL")
    op.alter_column("action_queue", "target_conversation_key", nullable=False)
    op.alter_column("action_queue", "conversation_id", nullable=False)
    op.drop_constraint(
        "fk_action_queue_job_id_jobs",
        "action_queue",
        type_="foreignkey",
    )
    op.drop_column("action_queue", "job_id")
