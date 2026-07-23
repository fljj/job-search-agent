"""清除灰度运行历史，保留候选人长期配置。"""

import argparse
from datetime import UTC, datetime

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from apps.api.app.core.database import SessionLocal, engine
from apps.api.app.models import entities as db
from apps.api.app.services.user_service import DEFAULT_USER_ID

PRESERVED_TABLES = {
    "alembic_version",
    "users",
    "candidate_profiles",
    "candidate_skills",
    "candidate_industry_experiences",
    "job_strategies",
    "job_title_rules",
    "work_mode_rules",
    "work_mode_locations",
    "salary_rules",
    "salary_score_bands",
    "industry_rules",
    "company_blacklists",
    "knowledge_items",
    "resumes",
    "automation_settings",
    "scheduling_preferences",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    database_name = make_url(str(engine.url)).database
    if database_name != "job_agent" or args.confirm_database != database_name:
        raise RuntimeError(
            "仅允许清理明确确认的本地 job_agent 数据库"
        )
    existing = set(inspect(engine).get_table_names())
    operational = sorted(existing - PRESERVED_TABLES)
    with SessionLocal() as session:
        preserved_counts = {
            table: session.scalar(
                text(f'SELECT count(*) FROM "{table}"')  # noqa: S608
            )
            for table in sorted(existing & PRESERVED_TABLES - {"alembic_version"})
        }
    print(f"database={database_name}")
    print(f"preserved_counts={preserved_counts}")
    print(f"truncate_tables={operational}")
    if not args.execute:
        print("dry-run only; add --execute to apply")
        return
    quoted = ", ".join(f'"{table}"' for table in operational)
    with engine.begin() as connection:
        if quoted:
            connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    with SessionLocal() as session:
        if session.get(db.User, DEFAULT_USER_ID):
            session.add(
                db.RolloutControl(
                    user_id=DEFAULT_USER_ID,
                    platform="BOSS",
                    status="PAUSED",
                    current_level=1,
                    previous_level=1,
                    stage_started_at=datetime.now(UTC),
                    minimum_stage_hours=24,
                    reply_daily_limit=5,
                    greeting_daily_limit=3,
                )
            )
            session.commit()
        remaining = {
            table: session.scalar(
                text(f'SELECT count(*) FROM "{table}"')  # noqa: S608
            )
            for table in operational
        }
        rollout = session.scalar(
            select(db.RolloutControl).where(
                db.RolloutControl.user_id == DEFAULT_USER_ID,
                db.RolloutControl.platform == "BOSS",
            )
        )
    print(f"remaining_operational_counts={remaining}")
    print(
        "rollout=BOSS:PAUSED:1"
        if rollout is not None
        else "rollout=not-created-no-default-user"
    )


if __name__ == "__main__":
    main()
