"""增加 LLM 基础数据、策略优先级和新自动化阈值。

Revision ID: 20260723_0007
Revises: 20260721_0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_0007"
down_revision = "20260721_0006"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _foreign_key_columns(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    }


def _drop_foreign_key_for_columns(table_name: str, columns: tuple[str, ...]) -> None:
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        if tuple(foreign_key["constrained_columns"]) == columns and foreign_key["name"]:
            op.drop_constraint(foreign_key["name"], table_name, type_="foreignkey")
            return


def _check_names(table_name: str) -> set[str]:
    return {
        check["name"]
        for check in sa.inspect(op.get_bind()).get_check_constraints(table_name)
        if check["name"]
    }


def upgrade() -> None:
    if "priority" not in _column_names("job_strategies"):
        op.add_column("job_strategies", sa.Column("priority", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY user_id ORDER BY created_at ASC, id ASC
            ) AS priority
            FROM job_strategies
        )
        UPDATE job_strategies
        SET priority = ranked.priority
        FROM ranked
        WHERE job_strategies.id = ranked.id
        """
    )
    op.alter_column(
        "job_strategies",
        "priority",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="100",
    )
    if "ck_job_strategies_priority_positive" not in _check_names("job_strategies"):
        op.create_check_constraint(
            "ck_job_strategies_priority_positive", "job_strategies", "priority >= 1"
        )

    op.create_table(
        "llm_invocations",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider_response_id", sa.String(200), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_llm_invocations_attempt_number"
        ),
    )
    op.create_index("ix_llm_invocations_input_hash", "llm_invocations", ["input_hash"])

    score_columns = _column_names("job_scores")
    if "prompt_version" not in score_columns:
        op.add_column(
            "job_scores",
            sa.Column(
                "prompt_version",
                sa.String(50),
                nullable=False,
                server_default="legacy-rules-v1",
            ),
        )
    if "llm_invocation_id" not in score_columns:
        op.add_column(
            "job_scores", sa.Column("llm_invocation_id", postgresql.UUID(), nullable=True)
        )
    if "llm_recommends_proactive_contact" not in score_columns:
        op.add_column(
            "job_scores",
            sa.Column(
                "llm_recommends_proactive_contact",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "llm_contact_reason" not in score_columns:
        op.add_column(
            "job_scores", sa.Column("llm_contact_reason", sa.Text(), nullable=True)
        )
    if "automation_eligible" not in score_columns:
        op.add_column(
            "job_scores",
            sa.Column(
                "automation_eligible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if ("llm_invocation_id",) not in _foreign_key_columns("job_scores"):
        op.create_foreign_key(
            "fk_job_scores_llm_invocation",
            "job_scores",
            "llm_invocations",
            ["llm_invocation_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        """
        UPDATE job_scores
        SET scoring_version = 'legacy:' || scoring_version
        WHERE scoring_version NOT LIKE 'legacy:%'
        """
    )

    if "evidence_refs" not in _column_names("job_score_details"):
        op.add_column(
            "job_score_details",
            sa.Column(
                "evidence_refs",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    conversation_columns = _column_names("conversations")
    if "strategy_id" not in conversation_columns:
        op.add_column(
            "conversations", sa.Column("strategy_id", postgresql.UUID(), nullable=True)
        )
    if "latest_job_score_id" not in conversation_columns:
        op.add_column(
            "conversations",
            sa.Column("latest_job_score_id", postgresql.UUID(), nullable=True),
        )
    conversation_foreign_keys = _foreign_key_columns("conversations")
    if ("strategy_id",) not in conversation_foreign_keys:
        op.create_foreign_key(
            "fk_conversations_strategy",
            "conversations",
            "job_strategies",
            ["strategy_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if ("latest_job_score_id",) not in conversation_foreign_keys:
        op.create_foreign_key(
            "fk_conversations_latest_job_score",
            "conversations",
            "job_scores",
            ["latest_job_score_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(
        """
        UPDATE automation_settings
        SET auto_greet_min_score = GREATEST(auto_greet_min_score, 80),
            auto_resume_min_score = GREATEST(auto_resume_min_score, 60)
        """
    )
    op.alter_column(
        "automation_settings", "auto_greet_min_score", server_default="80"
    )
    op.alter_column(
        "automation_settings", "auto_resume_min_score", server_default="60"
    )
    automation_checks = _check_names("automation_settings")
    if "ck_automation_greet_min_score" not in automation_checks:
        op.create_check_constraint(
            "ck_automation_greet_min_score",
            "automation_settings",
            "auto_greet_min_score BETWEEN 80 AND 100",
        )
    if "ck_automation_resume_min_score" not in automation_checks:
        op.create_check_constraint(
            "ck_automation_resume_min_score",
            "automation_settings",
            "auto_resume_min_score BETWEEN 60 AND 100",
        )


def downgrade() -> None:
    op.drop_constraint(
        "ck_automation_resume_min_score", "automation_settings", type_="check"
    )
    op.drop_constraint(
        "ck_automation_greet_min_score", "automation_settings", type_="check"
    )
    op.execute(
        """
        UPDATE automation_settings
        SET auto_greet_min_score = 70
        WHERE auto_greet_min_score = 80
        """
    )
    op.alter_column(
        "automation_settings", "auto_resume_min_score", server_default="70"
    )
    op.alter_column(
        "automation_settings", "auto_greet_min_score", server_default="70"
    )

    _drop_foreign_key_for_columns("conversations", ("latest_job_score_id",))
    _drop_foreign_key_for_columns("conversations", ("strategy_id",))
    op.drop_column("conversations", "latest_job_score_id")
    op.drop_column("conversations", "strategy_id")

    op.drop_column("job_score_details", "evidence_refs")
    op.execute(
        """
        UPDATE job_scores
        SET scoring_version = substring(scoring_version FROM 8)
        WHERE scoring_version LIKE 'legacy:%'
        """
    )
    _drop_foreign_key_for_columns("job_scores", ("llm_invocation_id",))
    op.drop_column("job_scores", "automation_eligible")
    op.drop_column("job_scores", "llm_contact_reason")
    op.drop_column("job_scores", "llm_recommends_proactive_contact")
    op.drop_column("job_scores", "llm_invocation_id")
    op.drop_column("job_scores", "prompt_version")

    op.drop_index("ix_llm_invocations_input_hash", table_name="llm_invocations")
    op.drop_table("llm_invocations")

    op.drop_constraint(
        "ck_job_strategies_priority_positive", "job_strategies", type_="check"
    )
    op.drop_column("job_strategies", "priority")
