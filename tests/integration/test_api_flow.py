import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from adapters.llm.fake import FakeLlmProvider
from apps.api.app.core.database import Base, get_session
from apps.api.app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL")
    engine = create_engine(database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_job_decision_api_is_idempotent_and_has_no_scores(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_response = client.put("/api/v1/profile", json={
        "name": "测试候选人", "total_years": 10, "management_years": 2,
        "has_architecture_experience": True,
        "has_core_system_experience": True,
        "bachelor_full_time": False,
        "skills": [{"name": "Java", "years": 10, "source": "测试", "is_core": True}],
        "industry_experiences": [{
            "industry_code": "互联网", "years": 10, "source": "测试",
        }],
    })
    assert profile_response.status_code == 200, profile_response.text
    profile = profile_response.json()["data"]
    strategy = client.post("/api/v1/strategies", json={
        "candidate_profile_id": profile["id"], "name": "Java岗位", "enabled": True,
        "priority": 100,
        "title_rules": [{"rule_type": "INCLUDE", "pattern": "Java"}],
        "accepted_seniority_levels": ["SENIOR"],
        "work_mode_rules": [{"work_mode": "REMOTE", "enabled": True,
                              "allowed_locations": [], "location_restricted": False}],
        "salary_rules": [{"work_mode": "REMOTE", "currency": "CNY",
                           "minimum_monthly_k": 20, "expected_monthly_k": 30}],
        "industry_rules": [{"industry": "互联网", "rule_type": "PREFERRED"}],
        "company_blacklist": [], "accept_outsourcing": False, "accept_part_time": True,
        "accept_headhunter": True, "max_posted_days": 30,
        "core_required_skills": ["Java"], "version": 1,
    }).json()["data"]
    job = client.post("/api/v1/jobs/import", json={
        "title": "高级Java开发", "company_name": "测试科技", "industry": "互联网",
        "location": "远程", "work_mode": "REMOTE", "salary_text": "30-40K",
        "description": "需要Java和Spring经验", "source_status": "OPEN", "source": "MOCK",
    }).json()["data"]["job"]

    monkeypatch.setattr(
        "apps.api.app.services.decision_service.build_runtime_llm_provider",
        lambda session: FakeLlmProvider(),
    )

    payload = {"strategy_id": strategy["id"], "candidate_profile_id": profile["id"]}
    first = client.post(f"/api/v1/jobs/{job['id']}/decisions", json=payload)
    conversation_payload = {
        "job_id": job["id"],
        "external_conversation_id": "decision-sync-conversation",
        "recruiter_name": "测试招聘人",
        "platform": "MOCK",
    }
    conversation = client.post("/api/v1/conversations", json=conversation_payload)
    assert conversation.status_code == 200, conversation.text
    second = client.post(f"/api/v1/jobs/{job['id']}/decisions", json=payload)

    assert first.status_code == 200, first.text
    assert first.json()["data"]["decision"] == "CONTACT"
    assert "total_score" not in first.json()["data"]
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    refreshed_conversation = client.post(
        "/api/v1/conversations", json=conversation_payload
    ).json()["data"]
    assert refreshed_conversation["latest_job_decision_id"] == first.json()["data"]["id"]
