"""replace seven-dimension scores with contact decisions

Revision ID: 20260802_0044
Revises: 20260802_0043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0044"
down_revision: str | None = "20260802_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("job_scores", "job_decisions")
    op.alter_column(
        "job_decisions", "scoring_version", new_column_name="decision_version"
    )
    op.alter_column(
        "job_decisions", "match_reasons", new_column_name="matched_evidence"
    )
    op.alter_column(
        "job_decisions", "risk_notes", new_column_name="uncertainties"
    )
    op.add_column(
        "job_decisions",
        sa.Column("decision", sa.String(20), nullable=False, server_default="SKIP"),
    )
    op.add_column(
        "job_decisions",
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1"),
    )
    op.add_column(
        "job_decisions",
        sa.Column("reason", sa.Text(), nullable=False, server_default="历史评分迁移记录"),
    )
    op.add_column(
        "job_decisions",
        sa.Column(
            "rejection_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE job_decisions
        SET decision = CASE
            WHEN hard_rejected THEN 'FILTERED_OUT'
            WHEN llm_recommends_proactive_contact THEN 'CONTACT'
            ELSE 'SKIP'
        END,
        reason = COALESCE(NULLIF(llm_contact_reason, ''), '历史评分迁移记录'),
        decision_version = 'migrated-from-seven-dimension-score:1'
        """
    )
    op.execute(
        """
        UPDATE job_decisions AS decision
        SET rejection_reasons = source.items
        FROM (
            SELECT job_score_id,
                   jsonb_agg(
                       jsonb_build_object(
                           'rule_code', rule_code,
                           'message', message,
                           'evidence', evidence
                       ) ORDER BY sort_order
                   ) AS items
            FROM job_rejections
            GROUP BY job_score_id
        ) AS source
        WHERE decision.id = source.job_score_id
        """
    )

    op.alter_column(
        "conversations", "latest_job_score_id", new_column_name="latest_job_decision_id"
    )
    op.alter_column(
        "generated_drafts", "job_score_id", new_column_name="job_decision_id"
    )
    op.alter_column(
        "job_discovery_records", "job_score_id", new_column_name="job_decision_id"
    )

    op.drop_table("job_score_details")
    op.drop_table("job_rejections")
    for column in (
        "title_score", "skill_score", "experience_score", "location_score",
        "salary_score", "industry_score", "management_score", "total_score",
        "grade", "eligibility", "llm_recommends_proactive_contact",
        "llm_contact_reason",
    ):
        op.drop_column("job_decisions", column)

    op.drop_constraint(
        "ck_job_strategies_headhunter_score_cap", "job_strategies", type_="check"
    )
    op.drop_column("job_strategies", "headhunter_score_cap")
    op.drop_column("job_title_rules", "score")
    op.drop_column("job_title_rules", "is_hard_requirement")
    op.drop_column("work_mode_rules", "location_score")
    op.drop_column("work_mode_rules", "unknown_score")
    op.drop_table("salary_score_bands")
    op.drop_column("salary_rules", "negotiable_score")
    op.drop_column("salary_rules", "unknown_score")
    op.drop_column("industry_rules", "score")
    op.drop_constraint(
        "ck_automation_greet_min_score", "automation_settings", type_="check"
    )
    op.drop_column("automation_settings", "auto_greet_min_score")


def downgrade() -> None:
    raise RuntimeError(
        "该迁移会移除七维明细，无法无损降级；请从迁移前数据库备份恢复。"
    )
