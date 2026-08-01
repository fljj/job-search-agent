"""add core status constraints and audit request id

Revision ID: 20260731_0038
Revises: 20260731_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0038"
down_revision: str | None = "20260731_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('RECEIVED','AWAITING_IDENTITY','SUPERSEDED','PROCESSING',"
        "'RETRY_WAIT','WAITING_FOR_LLM','QUARANTINED','COMPLETED',"
        "'MISMATCH_DECLINED','PLATFORM_EVENT_IGNORED')",
    )
    op.create_check_constraint(
        "ck_action_queue_status",
        "action_queue",
        "status IN ('PENDING_APPROVAL','APPROVED','EXECUTING','SUCCEEDED',"
        "'FAILED_RETRYABLE','FAILED_FINAL','CANCELLED','EXPIRED','SUPERSEDED',"
        "'OUTCOME_UNKNOWN')",
    )
    op.add_column("audit_events", sa.Column("request_id", sa.String(100), nullable=True))
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index(
        "uq_action_queue_draft_id",
        "action_queue",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("draft_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_action_queue_draft_id", table_name="action_queue")
    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    op.drop_column("audit_events", "request_id")
    op.drop_constraint("ck_action_queue_status", "action_queue", type_="check")
    op.drop_constraint("ck_messages_status", "messages", type_="check")
