"""对齐后期迁移时间戳列与 ORM 非空约束。

Revision ID: 20260726_0024
Revises: 20260724_0023
"""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0024"
down_revision = "20260724_0023"
branch_labels = None
depends_on = None

NON_NULL_TIMESTAMP_COLUMNS = {
    "job_discovery_records": ("created_at", "updated_at"),
    "platform_recommendations": (
        "first_observed_at",
        "last_observed_at",
        "created_at",
        "updated_at",
    ),
    "reconciliation_tasks": ("created_at", "updated_at"),
    "rollout_controls": ("created_at", "updated_at"),
    "worker_instances": ("started_at", "heartbeat_at"),
}


def upgrade() -> None:
    for table_name, column_names in NON_NULL_TIMESTAMP_COLUMNS.items():
        for column_name in column_names:
            op.execute(
                sa.text(
                    f'UPDATE "{table_name}" SET "{column_name}" = now() '
                    f'WHERE "{column_name}" IS NULL'
                )
            )
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )


def downgrade() -> None:
    for table_name, column_names in reversed(NON_NULL_TIMESTAMP_COLUMNS.items()):
        for column_name in reversed(column_names):
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
            )
