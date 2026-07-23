"""增加入站对话资格成熟度和动作授权快照。

Revision ID: 20260724_0021
Revises: 20260724_0020
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260724_0021"
down_revision = "20260724_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "qualification_status",
            sa.String(20),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "qualification_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "qualification_message_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "qualification_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "action_queue",
        sa.Column("authorization_basis", sa.String(80), nullable=True),
    )
    op.add_column(
        "action_queue",
        sa.Column(
            "qualification_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "action_queue",
        sa.Column(
            "evidence_message_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("action_queue", "evidence_message_ids")
    op.drop_column("action_queue", "qualification_snapshot")
    op.drop_column("action_queue", "authorization_basis")
    op.drop_column("conversations", "qualification_version")
    op.drop_column("conversations", "qualification_message_ids")
    op.drop_column("conversations", "qualification_evidence")
    op.drop_column("conversations", "qualification_status")
