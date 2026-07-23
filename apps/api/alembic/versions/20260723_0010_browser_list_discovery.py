"""保存招聘平台列表游标和脱敏提取结果。

Revision ID: 20260723_0010
Revises: 20260723_0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_0010"
down_revision = "20260723_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browser_read_runs", sa.Column("cursor", sa.String(500), nullable=True))
    op.add_column(
        "browser_read_runs",
        sa.Column(
            "extracted_items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("browser_read_runs", "extracted_items")
    op.drop_column("browser_read_runs", "cursor")
