import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from apps.api.app.core.database import Base, get_session
from apps.api.app.main import app
from apps.api.app.models import entities  # noqa: F401


@pytest.fixture
def client() -> Iterator[TestClient]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL")
    test_engine = create_engine(database_url)
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    factory = sessionmaker(test_engine, expire_on_commit=False)

    def override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


def test_complete_first_phase_api_flow(client: TestClient) -> None:
    profile_response = client.put("/api/v1/profile", json={
        "name": "集成测试候选人", "total_years": 13, "management_years": 5,
        "has_architecture_experience": True, "has_core_system_experience": True,
        "skills": [
            {"name": "Java", "years": 13, "source": "resume", "is_core": True},
            {"name": "Spring Boot", "years": 8, "source": "resume", "is_core": True},
            {"name": "MySQL", "years": 10, "source": "resume", "is_core": True},
        ],
        "industry_experiences": [{"industry_code": "互联网", "years": 10, "source": "resume"}],
    })
    assert profile_response.status_code == 200
    profile_id = profile_response.json()["data"]["id"]

    strategy_response = client.post("/api/v1/strategies", json={
        "candidate_profile_id": profile_id, "name": "集成测试策略", "enabled": True,
        "title_rules": [{"rule_type": "INCLUDE", "pattern": "Java后端", "score": 15}],
        "accepted_seniority_levels": ["MIDDLE", "SENIOR"],
        "work_mode_rules": [
            {"work_mode": "REMOTE", "enabled": True, "allowed_locations": [],
             "location_restricted": False, "score": 15, "unknown_score": 8},
        ],
        "salary_rules": [{
            "work_mode": "REMOTE", "currency": "CNY", "minimum_monthly_k": 35,
            "expected_monthly_k": 40, "negotiable_score": 8, "unknown_score": 8,
            "bands": [
                {"lower_bound_k": 0, "upper_bound_k": 40, "min_score": 0,
                 "max_score": 14, "interpolation": "LINEAR"},
                {"lower_bound_k": 40, "upper_bound_k": None, "min_score": 15,
                 "max_score": 15, "interpolation": "STEP"},
            ],
        }],
        "industry_rules": [{"industry": "互联网", "rule_type": "PREFERRED", "score": 10}],
        "company_blacklist": [], "accept_outsourcing": False, "accept_headhunter": True,
        "max_posted_days": 30, "core_required_skills": ["Java"], "version": 1,
    })
    assert strategy_response.status_code == 200
    strategy_id = strategy_response.json()["data"]["id"]

    profile_data = profile_response.json()["data"]
    profile_data["name"] = "集成测试候选人（已编辑）"
    profile_update = client.put("/api/v1/profile", json=profile_data)
    assert profile_update.status_code == 200
    assert profile_update.json()["data"]["version"] == 2
    profile_id = profile_update.json()["data"]["id"]

    strategy_data = strategy_response.json()["data"]
    strategy_data["name"] = "集成测试策略（已编辑）"
    strategy_update = client.put(f"/api/v1/strategies/{strategy_id}", json=strategy_data)
    assert strategy_update.status_code == 200
    assert strategy_update.json()["data"]["version"] == 2

    job_payload = {
        "external_job_id": "integration-001", "title": "Java后端工程师",
        "company_name": "集成测试公司", "industry": "互联网", "location": "北京",
        "work_mode": "REMOTE", "salary_text": "40K-45K",
        "description": "5年以上Java、Spring Boot和MySQL经验", "source_status": "OPEN",
    }
    first_import = client.post("/api/v1/jobs/import", json=job_payload)
    duplicate_import = client.post("/api/v1/jobs/import", json=job_payload)
    assert first_import.json()["data"]["result"] == "CREATED"
    assert duplicate_import.json()["data"]["result"] == "DUPLICATE"
    job_id = first_import.json()["data"]["job"]["id"]
    batch_import = client.post("/api/v1/jobs/import/batch", json={
        "items": [job_payload, {"title": "缺少必填字段"}],
    })
    assert batch_import.status_code == 200
    assert [item["result"] for item in batch_import.json()["data"]["items"]] == [
        "DUPLICATE", "VALIDATION_FAILED",
    ]

    parsed = client.post(f"/api/v1/jobs/{job_id}/parse", json={"mode": "RULE"})
    assert parsed.status_code == 200
    parsed_id = parsed.json()["data"]["id"]
    parsed_history = client.get(f"/api/v1/jobs/{job_id}/parsed-details")
    assert parsed_history.json()["data"]["total"] == 1
    parsed_detail = client.get(f"/api/v1/jobs/{job_id}/parsed-details/{parsed_id}")
    assert parsed_detail.status_code == 200

    score_payload = {"strategy_id": strategy_id, "candidate_profile_id": profile_id,
                     "parsed_job_detail_id": parsed_id}
    first_score = client.post(f"/api/v1/jobs/{job_id}/scores", json=score_payload)
    duplicate_score = client.post(f"/api/v1/jobs/{job_id}/scores", json=score_payload)
    assert first_score.status_code == 200
    assert first_score.json()["data"]["grade"] == "A"
    assert first_score.json()["data"]["eligibility"] == "ELIGIBLE"
    assert duplicate_score.json()["data"]["id"] == first_score.json()["data"]["id"]
    score_history = client.get(f"/api/v1/jobs/{job_id}/scores?strategy_id={strategy_id}")
    assert score_history.json()["data"]["total"] == 1
    batch_score = client.post("/api/v1/jobs/scores/batch", json={
        "job_ids": [job_id], "strategy_id": strategy_id,
        "candidate_profile_id": profile_id,
    })
    assert batch_score.json()["data"]["items"][0]["result"] == "SCORED"
