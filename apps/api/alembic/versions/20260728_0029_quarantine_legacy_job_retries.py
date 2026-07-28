"""隔离缺少调度信息的历史职位重试。

Revision ID: 20260728_0029
Revises: 20260728_0028
"""

from alembic import op

revision = "20260728_0029"
down_revision = "20260728_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE job_discovery_records
        SET status = 'SKIPPED',
            reason_codes = reason_codes || '["LEGACY_RETRY_UNSCHEDULED"]'::jsonb
        WHERE status = 'RETRYABLE'
          AND next_retry_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE job_discovery_records
        SET status = 'RETRYABLE',
            reason_codes = reason_codes - 'LEGACY_RETRY_UNSCHEDULED'
        WHERE reason_codes ? 'LEGACY_RETRY_UNSCHEDULED'
        """
    )
