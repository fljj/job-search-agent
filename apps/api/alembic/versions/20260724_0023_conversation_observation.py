"""保存入站页面观察快照并保留删除职位后的审计记录。

Revision ID: 20260724_0023
Revises: 20260724_0022
"""

import sqlalchemy as sa
from alembic import op

revision = "20260724_0023"
down_revision = "20260724_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("conversations")
    }
    if "observed_company_name" not in columns:
        op.add_column(
            "conversations",
            sa.Column("observed_company_name", sa.String(200), nullable=True),
        )
    if "observed_job_title" not in columns:
        op.add_column(
            "conversations",
            sa.Column("observed_job_title", sa.String(200), nullable=True),
        )
    if "observed_external_job_id" not in columns:
        op.add_column(
            "conversations",
            sa.Column("observed_external_job_id", sa.String(200), nullable=True),
        )
    op.drop_constraint(
        "conversations_job_id_fkey",
        "conversations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "conversations_job_id_fkey",
        "conversations",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_action_queue_job_id_jobs",
        "action_queue",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_action_queue_job_id_jobs",
        "action_queue",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_action_queue_job_id_jobs",
        "action_queue",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_action_queue_job_id_jobs",
        "action_queue",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "conversations_job_id_fkey",
        "conversations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "conversations_job_id_fkey",
        "conversations",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("conversations", "observed_external_job_id")
    op.drop_column("conversations", "observed_job_title")
    op.drop_column("conversations", "observed_company_name")
