import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from adapters.browser.maimai_recommendations import MaimaiRecommendationCard
from apps.api.app.core.database import Base
from apps.api.app.models import entities as db
from apps.api.app.services.operations_service import audit_discrepancies
from apps.api.app.services.recommendation_service import (
    dispatch_recommendation,
    scan_recommendations,
)
from apps.api.app.services.rollout_service import calculate_safety_metrics
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.actions import ExecutionOutcome, ExecutionResult
from packages.policy_engine.recommendation import RecommendationRules


@pytest.fixture
def session() -> Iterator[Session]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL")
    engine = create_engine(database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value
    Base.metadata.drop_all(engine)
    engine.dispose()


class FakeRecommendationAdapter:
    def __init__(self, card: MaimaiRecommendationCard) -> None:
        self.card = card

    def scan(
        self,
        _cdp_url: str,
        _rules: RecommendationRules,
        limit: int = 20,
    ) -> list[MaimaiRecommendationCard]:
        return [self.card]


class EmptyRecommendationAdapter:
    def scan(
        self,
        _cdp_url: str,
        _rules: RecommendationRules,
        limit: int = 20,
    ) -> list[MaimaiRecommendationCard]:
        return []


class SuccessfulExecutor:
    def execute(self, *_: object, **__: object) -> ExecutionResult:
        return ExecutionResult(
            outcome=ExecutionOutcome.SUCCEEDED,
            evidence_hash="a" * 64,
            observed_content="已发送简历",
        )


def test_recommendation_scan_is_idempotent_and_dispatches_once(
    session: Session,
) -> None:
    session.add(db.User(id=DEFAULT_USER_ID, display_name="测试用户"))
    session.flush()
    profile = db.CandidateProfile(
        user_id=DEFAULT_USER_ID,
        name="测试候选人",
        total_years=10,
        management_years=0,
        has_architecture_experience=True,
        has_core_system_experience=True,
    )
    session.add(profile)
    session.flush()
    strategy = db.JobStrategy(
        user_id=DEFAULT_USER_ID,
        candidate_profile_id=profile.id,
        name="测试策略",
        enabled=True,
        priority=1,
    )
    session.add(strategy)
    session.flush()
    session.add(
        db.AutomationSetting(
            user_id=DEFAULT_USER_ID,
            scope_type="GLOBAL",
            scope_key="GLOBAL",
            enabled=True,
            auto_resume_enabled=True,
            maimai_recommendation_enabled=True,
            maimai_recommendation_resume_enabled=True,
        )
    )
    session.add(
        db.RolloutControl(
            user_id=DEFAULT_USER_ID,
            platform="MAIMAI",
            status="ACTIVE",
            current_level=5,
            previous_level=4,
            stage_started_at=datetime.now(UTC),
        )
    )
    run = db.AgentRun(
        user_id=DEFAULT_USER_ID,
        platform="MAIMAI",
        strategy_id=strategy.id,
        executor_type="REAL_CDP",
        status="RUNNING",
        cursor={},
    )
    session.add(run)
    session.commit()
    card = MaimaiRecommendationCard(
        external_recommendation_id="recommendation-1",
        recruiter_name="猎头顾问",
        recruiter_title="示例公司·顾问",
        company_name="示例公司",
        job_title="Java 后端开发",
        card_text="系统推荐 Java 后端开发，可以要一份你的简历吗",
    )
    adapter = FakeRecommendationAdapter(card)

    first = scan_recommendations(
        session, run, "http://127.0.0.1:9222", adapter=adapter
    )
    repeated = scan_recommendations(
        session, run, "http://127.0.0.1:9222", adapter=adapter
    )

    assert first[0]["decision"] == "ACCEPT_AND_SEND_PROFILE"
    assert first[0]["action_status"] == "APPROVED"
    assert repeated[0]["id"] == first[0]["id"]
    actions = session.scalars(select(db.ActionQueue)).all()
    assert len(actions) == 1

    completed = dispatch_recommendation(
        session,
        UUID(str(first[0]["id"])),
        "http://127.0.0.1:9222",
        executor=SuccessfulExecutor(),
    )
    repeated_completion = dispatch_recommendation(
        session,
        UUID(str(first[0]["id"])),
        "http://127.0.0.1:9222",
        executor=SuccessfulExecutor(),
    )
    assert completed["status"] == "ACCEPTED"
    assert repeated_completion["status"] == "ACCEPTED"
    attempts = session.scalars(select(db.ActionAttempt)).all()
    assert len(attempts) == 1
    rollout = session.scalar(
        select(db.RolloutControl).where(db.RolloutControl.platform == "MAIMAI")
    )
    assert rollout is not None
    assert calculate_safety_metrics(session, rollout)["UNSCORED_WRITE"] == 0
    assert audit_discrepancies(session) == []


def test_recommendation_is_authorized_after_rollout_is_configured(
    session: Session,
) -> None:
    session.add(db.User(id=DEFAULT_USER_ID, display_name="测试用户"))
    session.flush()
    profile = db.CandidateProfile(
        user_id=DEFAULT_USER_ID,
        name="测试候选人",
        total_years=10,
        management_years=0,
        has_architecture_experience=True,
        has_core_system_experience=True,
    )
    session.add(profile)
    session.flush()
    strategy = db.JobStrategy(
        user_id=DEFAULT_USER_ID,
        candidate_profile_id=profile.id,
        name="测试策略",
        enabled=True,
        priority=1,
    )
    session.add(strategy)
    session.flush()
    session.add(
        db.AutomationSetting(
            user_id=DEFAULT_USER_ID,
            scope_type="GLOBAL",
            scope_key="GLOBAL",
            enabled=True,
            auto_resume_enabled=True,
            maimai_recommendation_enabled=True,
            maimai_recommendation_resume_enabled=True,
        )
    )
    run = db.AgentRun(
        user_id=DEFAULT_USER_ID,
        platform="MAIMAI",
        strategy_id=strategy.id,
        executor_type="REAL_CDP",
        status="RUNNING",
        cursor={},
    )
    session.add(run)
    session.commit()
    adapter = FakeRecommendationAdapter(
        MaimaiRecommendationCard(
            external_recommendation_id="recommendation-delayed-rollout",
            recruiter_name="招聘顾问",
            recruiter_title="示例公司·招聘",
            company_name="示例公司",
            job_title="Java 后端开发",
            card_text="系统推荐 Java 后端开发，可以要一份你的简历吗",
        )
    )

    first = scan_recommendations(
        session, run, "http://127.0.0.1:9222", adapter=adapter
    )[0]
    assert first["action_id"] is None
    assert "ROLLOUT_NOT_CONFIGURED" in first["reason_codes"]

    session.add(
        db.RolloutControl(
            user_id=DEFAULT_USER_ID,
            platform="MAIMAI",
            status="ACTIVE",
            current_level=6,
            previous_level=1,
            stage_started_at=datetime.now(UTC),
        )
    )
    session.commit()
    authorized = scan_recommendations(
        session,
        run,
        "http://127.0.0.1:9222",
        adapter=EmptyRecommendationAdapter(),
    )[0]

    assert authorized["action_status"] == "APPROVED"
    assert "ROLLOUT_NOT_CONFIGURED" not in authorized["reason_codes"]
