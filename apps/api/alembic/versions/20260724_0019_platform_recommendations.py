"""增加脉脉平台推荐记录。

Revision ID: 20260724_0019
Revises: 20260723_0018
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260724_0019"
down_revision = "20260723_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_settings",
        sa.Column(
            "maimai_recommendation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "maimai_recommendation_resume_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "platform_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("external_recommendation_id", sa.String(200), nullable=False),
        sa.Column("recruiter_name", sa.String(100), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("job_title", sa.String(200), nullable=False),
        sa.Column("location", sa.String(150), nullable=True),
        sa.Column("salary_text", sa.String(200), nullable=True),
        sa.Column("description_summary", sa.Text(), nullable=True),
        sa.Column("card_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(50), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(30), nullable=False, server_default="DECIDED"),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observed_result", sa.Text(), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "platform", "external_recommendation_id"),
    )
    op.add_column("action_queue", sa.Column("platform_recommendation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_action_queue_platform_recommendation",
        "action_queue", "platform_recommendations",
        ["platform_recommendation_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_platform_recommendations_action",
        "platform_recommendations", "action_queue",
        ["action_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_platform_recommendations_action", "platform_recommendations", type_="foreignkey")
    op.drop_constraint("fk_action_queue_platform_recommendation", "action_queue", type_="foreignkey")
    op.drop_column("action_queue", "platform_recommendation_id")
    op.drop_table("platform_recommendations")
    op.drop_column("automation_settings", "maimai_recommendation_resume_enabled")
    op.drop_column("automation_settings", "maimai_recommendation_enabled")
