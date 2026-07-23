"""移除动作队列到推荐记录的冗余反向外键。

Revision ID: 20260724_0020
Revises: 20260724_0019
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260724_0020"
down_revision = "20260724_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_action_queue_platform_recommendation",
        "action_queue",
        type_="foreignkey",
    )
    op.drop_column("action_queue", "platform_recommendation_id")


def downgrade() -> None:
    op.add_column(
        "action_queue",
        sa.Column(
            "platform_recommendation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_action_queue_platform_recommendation",
        "action_queue",
        "platform_recommendations",
        ["platform_recommendation_id"],
        ["id"],
        ondelete="SET NULL",
    )
