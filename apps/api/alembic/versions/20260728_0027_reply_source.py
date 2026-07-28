"""记录回复草稿来源。

Revision ID: 20260728_0027
Revises: 20260728_0026
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_0027"
down_revision = "20260728_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史草稿无法可靠反推来源，保留 NULL；应用层保证新草稿写入来源。
    op.add_column(
        "generated_drafts",
        sa.Column("reply_source", sa.String(length=30), nullable=True),
    )
    op.create_check_constraint(
        "ck_generated_drafts_reply_source",
        "generated_drafts",
        "reply_source IS NULL OR reply_source IN "
        "('RULE_TEMPLATE', 'KNOWLEDGE_BASE', 'LLM', 'HUMAN')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_generated_drafts_reply_source",
        "generated_drafts",
        type_="check",
    )
    op.drop_column("generated_drafts", "reply_source")
