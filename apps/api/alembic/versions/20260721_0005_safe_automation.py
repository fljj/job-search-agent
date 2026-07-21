"""增加第五阶段多层自动化设置与动作授权来源。

Revision ID: 20260721_0005
Revises: 20260721_0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0005"
down_revision = "20260721_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("action_queue", "confirmation_task_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column("action_queue", sa.Column("policy_decision_id", postgresql.UUID(), nullable=True))
    op.add_column("action_queue", sa.Column("strategy_id", postgresql.UUID(), nullable=True))
    op.add_column("action_queue", sa.Column("authorization_source", sa.String(20), nullable=False, server_default="MANUAL"))
    op.create_foreign_key("fk_action_policy_decision", "action_queue", "policy_decisions", ["policy_decision_id"], ["id"])
    op.create_foreign_key("fk_action_strategy", "action_queue", "job_strategies", ["strategy_id"], ["id"])
    op.create_table(
        "automation_settings",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("user_id", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_key", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_greet_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_greet_min_score", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("auto_reply_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_reply_min_confidence", sa.Numeric(3, 2), nullable=False, server_default="0.90"),
        sa.Column("auto_resume_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_resume_min_score", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("hourly_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "scope_type", "scope_key"),
    )


def downgrade() -> None:
    op.drop_table("automation_settings")
    op.drop_constraint("fk_action_strategy", "action_queue", type_="foreignkey")
    op.drop_constraint("fk_action_policy_decision", "action_queue", type_="foreignkey")
    op.drop_column("action_queue", "authorization_source")
    op.drop_column("action_queue", "strategy_id")
    op.drop_column("action_queue", "policy_decision_id")
    op.alter_column("action_queue", "confirmation_task_id", existing_type=postgresql.UUID(), nullable=False)
