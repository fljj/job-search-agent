"""增加消息发现的会话级短租约。

Revision ID: 20260723_0015
Revises: 20260723_0014
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_0015"
down_revision = "20260723_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("processing_lease_owner", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "processing_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "processing_lease_expires_at")
    op.drop_column("conversations", "processing_lease_owner")
