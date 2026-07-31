"""bind LLM parse inputs and persist calendar query evidence

Revision ID: 20260731_0037
Revises: 20260731_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0037"
down_revision: str | None = "20260731_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("parsed_job_details", sa.Column("input_fingerprint", sa.String(64), nullable=True))
    op.add_column("parsed_job_details", sa.Column("provider", sa.String(30), nullable=True))
    op.add_column("parsed_job_details", sa.Column("model", sa.String(100), nullable=True))
    op.add_column("parsed_job_details", sa.Column("prompt_version", sa.String(50), nullable=True))
    op.add_column("parsed_job_details", sa.Column("schema_version", sa.String(50), nullable=True))
    op.add_column("parsed_job_details", sa.Column("llm_invocation_id", postgresql.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_parsed_job_details_llm_invocation",
        "parsed_job_details",
        "llm_invocations",
        ["llm_invocation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_parsed_job_details_input_fingerprint",
        "parsed_job_details",
        ["input_fingerprint"],
        unique=True,
    )
    op.add_column("calendar_checks", sa.Column("provider", sa.String(30), server_default="MOCK", nullable=False))
    op.add_column("calendar_checks", sa.Column("query_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calendar_checks", sa.Column("query_end_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calendar_checks", sa.Column("timezone", sa.String(80), server_default="Asia/Shanghai", nullable=False))
    op.add_column("calendar_checks", sa.Column("query_evidence", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("schedule_confirmations", sa.Column("reply_source", sa.String(30), server_default="HUMAN", nullable=False))


def downgrade() -> None:
    op.drop_column("schedule_confirmations", "reply_source")
    for column in ("query_evidence", "timezone", "query_end_at", "query_start_at", "provider"):
        op.drop_column("calendar_checks", column)
    op.drop_index("ix_parsed_job_details_input_fingerprint", table_name="parsed_job_details")
    op.drop_constraint("fk_parsed_job_details_llm_invocation", "parsed_job_details", type_="foreignkey")
    for column in ("llm_invocation_id", "schema_version", "prompt_version", "model", "provider", "input_fingerprint"):
        op.drop_column("parsed_job_details", column)
