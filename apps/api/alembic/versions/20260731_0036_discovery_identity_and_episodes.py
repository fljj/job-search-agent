"""add discovery identity, retries, observations and page ownership

Revision ID: 20260731_0036
Revises: 20260731_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0036"
down_revision: str | None = "20260731_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_observations",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("job_id", postgresql.UUID(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "content_hash"),
    )
    op.create_index("ix_job_observations_job_observed", "job_observations", ["job_id", "observed_at"])
    op.add_column("conversations", sa.Column("recruiter_role", sa.String(30), server_default="UNKNOWN", nullable=False))
    op.add_column("conversations", sa.Column("identity_reliable", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("conversations", sa.Column("episode_number", sa.Integer(), server_default="1", nullable=False))
    op.add_column("conversations", sa.Column("terminal_message_id", sa.String(200), nullable=True))
    op.add_column("conversations", sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("identity_reliable", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("messages", sa.Column("episode_number", sa.Integer(), server_default="1", nullable=False))
    op.add_column("messages", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("messages", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("error_code", sa.String(100), nullable=True))
    op.add_column("messages", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_discovery_records", sa.Column("prefilter_state", sa.String(20), server_default="UNKNOWN", nullable=False))
    op.add_column("job_discovery_records", sa.Column("prefilter_reason", sa.String(100), nullable=True))
    op.create_table(
        "browser_page_registrations",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("page_role", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(200), nullable=False),
        sa.Column("agent_owned", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "page_role"),
    )


def downgrade() -> None:
    op.drop_table("browser_page_registrations")
    op.drop_column("job_discovery_records", "prefilter_reason")
    op.drop_column("job_discovery_records", "prefilter_state")
    for column in ("quarantined_at", "processing_started_at", "error_code", "retry_at", "attempt_count", "episode_number", "identity_reliable"):
        op.drop_column("messages", column)
    for column in ("terminal_at", "terminal_message_id", "episode_number", "identity_reliable", "recruiter_role"):
        op.drop_column("conversations", column)
    op.drop_index("ix_job_observations_job_observed", table_name="job_observations")
    op.drop_table("job_observations")
