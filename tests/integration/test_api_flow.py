import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adapters.llm.fake import FakeLlmProvider
from apps.api.app.core.database import Base, get_session
from apps.api.app.main import app
from apps.api.app.models import entities  # noqa: F401
from apps.api.app.schemas.browser import BrowserReadRequest
from apps.api.app.services.browser_service import persist_read_result
from packages.browser_worker.actions import ExecutionOutcome, ExecutionResult
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserJob,
    BrowserMessage,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


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


def test_complete_first_phase_api_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.api.app.services.score_service.build_llm_provider",
        lambda _: FakeLlmProvider(),
    )
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
    assert strategy_response.json()["data"]["priority"] == 100
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
    assert first_score.status_code == 200, first_score.text
    assert first_score.json()["data"]["grade"] == "A"
    assert first_score.json()["data"]["eligibility"] == "ELIGIBLE"
    assert first_score.json()["data"]["scoring_version"].startswith("llm:")
    assert first_score.json()["data"]["llm_invocation_id"] is not None
    assert duplicate_score.json()["data"]["id"] == first_score.json()["data"]["id"]
    reassessed = client.post(
        f"/api/v1/jobs/{job_id}/scores/re-evaluate",
        json=score_payload,
        headers={"Idempotency-Key": "integration-score-reassess"},
    )
    duplicate_reassessment = client.post(
        f"/api/v1/jobs/{job_id}/scores/re-evaluate",
        json=score_payload,
        headers={"Idempotency-Key": "integration-score-reassess"},
    )
    assert reassessed.json()["data"]["id"] != first_score.json()["data"]["id"]
    assert duplicate_reassessment.json()["data"]["id"] == reassessed.json()["data"]["id"]
    score_history = client.get(f"/api/v1/jobs/{job_id}/scores?strategy_id={strategy_id}")
    assert score_history.json()["data"]["total"] == 2
    batch_score = client.post("/api/v1/jobs/scores/batch", json={
        "job_ids": [job_id], "strategy_id": strategy_id,
        "candidate_profile_id": profile_id,
    })
    assert batch_score.json()["data"]["items"][0]["result"] == "SCORED"

    knowledge = client.post("/api/v1/knowledge-items", json={
        "category": "TECH_STACK", "key": "Java",
        "fact": "拥有 13 年 Java 后端开发经验", "source": "用户确认",
        "allowed_for_auto_reply": True, "sensitivity": "NORMAL",
        "verified_at": datetime.now(UTC).isoformat(),
    })
    assert knowledge.status_code == 200
    assert client.get("/api/v1/knowledge-items").json()["data"]["total"] == 1

    resume = client.post("/api/v1/resumes", json={
        "platform": "MOCK", "attachment_name": "Java后端简历.pdf",
        "target_directions": ["Java后端"], "is_available": True,
    })
    assert resume.status_code == 200
    selected_resume = client.get(f"/api/v1/resumes/select?job_id={job_id}")
    assert selected_resume.json()["data"]["attachment_name"] == "Java后端简历.pdf"

    greeting = client.post("/api/v1/drafts/greeting", json={
        "job_score_id": first_score.json()["data"]["id"],
    })
    assert greeting.status_code == 200
    assert greeting.json()["data"]["decision"] == "ALLOW_AUTO"
    assert "13 年" in greeting.json()["data"]["content"]

    conversation = client.post("/api/v1/conversations", json={
        "job_id": job_id, "external_conversation_id": "integration-conversation-1",
        "recruiter_name": "集成测试招聘人", "platform": "MOCK",
    })
    conversation_id = conversation.json()["data"]["id"]
    technical_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-1", "content": "请介绍 Java 技术栈经验",
        "received_at": datetime.now(UTC).isoformat(),
    })
    message_id = technical_message.json()["data"]["id"]
    reply = client.post("/api/v1/drafts/reply", json={"message_id": message_id})
    duplicate_reply = client.post("/api/v1/drafts/reply", json={"message_id": message_id})
    assert reply.json()["data"]["decision"] == "ALLOW_AUTO"
    assert duplicate_reply.json()["data"]["id"] == reply.json()["data"]["id"]

    automation_setting = {
        "scope_type": "GLOBAL", "scope_key": "GLOBAL", "enabled": False,
        "paused": False, "auto_greet_enabled": True, "auto_greet_min_score": 80,
        "auto_reply_enabled": True, "auto_reply_min_confidence": 0.9,
        "auto_resume_enabled": True, "auto_resume_min_score": 60,
        "hourly_limit": 10, "daily_limit": 50,
    }
    saved_setting = client.put("/api/v1/automation/settings", json=automation_setting)
    assert saved_setting.status_code == 200
    assert client.get("/api/v1/automation/settings").json()["data"]["items"][0]["enabled"] is False
    denied_auto = client.post("/api/v1/automation/dispatch", json={
        "action_type": "REPLY", "conversation_id": conversation_id,
        "draft_id": reply.json()["data"]["id"], "cdp_url": "http://127.0.0.1:9222",
    })
    assert denied_auto.json()["data"]["decision"] == "DENY"
    automation_setting["enabled"] = True
    assert client.put("/api/v1/automation/settings", json=automation_setting).status_code == 200
    auto_conversation = client.post("/api/v1/conversations", json={
        "job_id": job_id, "external_conversation_id": "integration-auto-conversation",
        "recruiter_name": "自动化测试招聘人", "platform": "MOCK",
    }).json()["data"]
    auto_message = client.post(
        f"/api/v1/conversations/{auto_conversation['id']}/messages",
        json={"external_message_id": "auto-message-1", "content": "请介绍 Java 技术栈经验",
              "received_at": datetime.now(UTC).isoformat()},
    ).json()["data"]
    auto_draft = client.post("/api/v1/drafts/reply", json={"message_id": auto_message["id"]}).json()["data"]
    monkeypatch.setattr(
        "adapters.browser.playwright_actions.PlaywrightActionExecutor.execute",
        lambda *_: ExecutionResult(outcome=ExecutionOutcome.SUCCEEDED, evidence_hash="a" * 64),
    )
    auto_sent = client.post("/api/v1/automation/dispatch", json={
        "action_type": "REPLY", "conversation_id": auto_conversation["id"],
        "draft_id": auto_draft["id"], "cdp_url": "http://127.0.0.1:9222",
    })
    assert auto_sent.json()["data"]["decision"] == "ALLOW_AUTO"

    time_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-2", "content": "周二几点可以电话面试？",
        "received_at": datetime.now(UTC).isoformat(),
    })
    time_reply = client.post("/api/v1/drafts/reply", json={
        "message_id": time_message.json()["data"]["id"],
    })
    assert time_reply.json()["data"]["decision"] == "REQUIRE_CONFIRMATION"
    assert time_reply.json()["data"]["confirmation_task_id"] is not None
    tasks = client.get("/api/v1/confirmation-tasks")
    assert len(tasks.json()["data"]["items"]) >= 3

    reply_task_id = reply.json()["data"]["confirmation_task_id"]
    approval = client.post(
        f"/api/v1/confirmation-tasks/{reply_task_id}/approve",
        json={"conversation_id": conversation_id},
        headers={"Idempotency-Key": "integration-reply-action"},
    )
    duplicate_approval = client.post(
        f"/api/v1/confirmation-tasks/{reply_task_id}/approve",
        json={"conversation_id": conversation_id},
        headers={"Idempotency-Key": "integration-reply-action"},
    )
    assert approval.status_code == 200
    assert duplicate_approval.json()["data"]["id"] == approval.json()["data"]["id"]
    duplicate_content = client.post(
        f"/api/v1/confirmation-tasks/{reply_task_id}/approve",
        json={"conversation_id": conversation_id},
        headers={"Idempotency-Key": "integration-reply-action-other-key"},
    )
    assert duplicate_content.status_code == 400

    monkeypatch.setattr(
        "adapters.browser.playwright_actions.PlaywrightActionExecutor.execute",
        lambda *_: ExecutionResult(outcome=ExecutionOutcome.SUCCEEDED, evidence_hash="c" * 64),
    )
    executed = client.post(
        f"/api/v1/actions/{approval.json()['data']['id']}/execute",
        json={"cdp_url": "http://127.0.0.1:9222"},
    )
    assert executed.json()["data"]["status"] == "SUCCEEDED"

    greeting_task_id = greeting.json()["data"]["confirmation_task_id"]
    reused_key = client.post(
        f"/api/v1/confirmation-tasks/{greeting_task_id}/approve",
        json={"conversation_id": conversation_id},
        headers={"Idempotency-Key": "integration-reply-action"},
    )
    assert reused_key.status_code == 400
    greeting_action = client.post(
        f"/api/v1/confirmation-tasks/{greeting_task_id}/approve",
        json={"conversation_id": conversation_id},
        headers={"Idempotency-Key": "integration-greeting-action"},
    ).json()["data"]
    monkeypatch.setattr(
        "adapters.browser.playwright_actions.PlaywrightActionExecutor.execute",
        lambda *_: ExecutionResult(
            outcome=ExecutionOutcome.OUTCOME_UNKNOWN, error_code="RESULT_NOT_OBSERVED"
        ),
    )
    unknown = client.post(
        f"/api/v1/actions/{greeting_action['id']}/execute",
        json={"cdp_url": "http://127.0.0.1:9222"},
    )
    assert unknown.json()["data"]["status"] == "OUTCOME_UNKNOWN"
    assert client.post(f"/api/v1/actions/{greeting_action['id']}/retry").status_code == 400

    time_task_id = time_reply.json()["data"]["confirmation_task_id"]
    unsafe_edit = client.post(f"/api/v1/confirmation-tasks/{time_task_id}/modify",
                              json={"content": "我的身份证号是 123"})
    assert unsafe_edit.status_code == 400
    safe_edit = client.post(f"/api/v1/confirmation-tasks/{time_task_id}/modify",
                            json={"content": "这个时间我需要确认后再回复您。"})
    assert safe_edit.status_code == 200
    rejected = client.post(
        f"/api/v1/confirmation-tasks/{safe_edit.json()['data']['id']}/reject"
    )
    assert rejected.json()["data"]["status"] == "CANCELLED"

    unavailable_schedule = client.post("/api/v1/scheduling/analyze", json={
        "message_id": time_message.json()["data"]["id"], "calendar_available": False,
    })
    assert unavailable_schedule.json()["data"]["calendar_status"] == "UNAVAILABLE"
    assert unavailable_schedule.json()["data"]["status"] == "PENDING_APPROVAL"

    explicit_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-schedule-explicit",
        "content": "2026-07-24 10:00 可以电话沟通吗，北京时间",
        "received_at": datetime.now(UTC).isoformat(),
    }).json()["data"]
    schedule = client.post("/api/v1/scheduling/analyze", json={
        "message_id": explicit_message["id"], "calendar_available": True,
    }).json()["data"]
    assert schedule["calendar_status"] == "AVAILABLE"
    approved_schedule = client.post(f"/api/v1/scheduling/requests/{schedule['id']}/approve", json={
        "reply_content": schedule["suggested_reply"], "create_calendar_event": True,
    })
    assert approved_schedule.json()["data"]["status"] == "APPROVED"
    monkeypatch.setattr(
        "adapters.browser.playwright_actions.PlaywrightActionExecutor.execute",
        lambda *_: ExecutionResult(outcome=ExecutionOutcome.SUCCEEDED, evidence_hash="d" * 64),
    )
    sent_schedule = client.post(f"/api/v1/scheduling/requests/{schedule['id']}/execute", json={
        "cdp_url": "http://127.0.0.1:9222",
    })
    assert sent_schedule.json()["data"]["status"] == "SUCCEEDED"

    changed_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-schedule-changed",
        "content": "2026-07-24 15:00 可以视频面试吗，北京时间",
        "received_at": datetime.now(UTC).isoformat(),
    }).json()["data"]
    changed_schedule = client.post("/api/v1/scheduling/analyze", json={
        "message_id": changed_message["id"], "calendar_available": True,
    }).json()["data"]
    client.post(f"/api/v1/scheduling/requests/{changed_schedule['id']}/approve", json={
        "reply_content": changed_schedule["suggested_reply"], "create_calendar_event": False,
    })
    client.post("/api/v1/scheduling/calendar-events", json={
        "external_event_id": "new-conflict", "title": "新增忙碌",
        "start_at": "2026-07-24T14:45:00+08:00", "end_at": "2026-07-24T16:30:00+08:00",
        "availability": "BUSY",
    })
    blocked_schedule = client.post(
        f"/api/v1/scheduling/requests/{changed_schedule['id']}/execute",
        json={"cdp_url": "http://127.0.0.1:9222"},
    )
    assert blocked_schedule.status_code == 400
    changed_item = next(item for item in client.get("/api/v1/scheduling/requests").json()["data"]["items"]
                        if item["id"] == changed_schedule["id"])
    assert changed_item["status"] == "PENDING_APPROVAL"


def test_browser_read_persistence_is_idempotent(client: TestClient) -> None:
    job_payload = {
        "external_job_id": "browser-parent-job", "title": "Java后端工程师",
        "company_name": "浏览器测试公司", "industry": "互联网", "location": "北京",
        "work_mode": "REMOTE", "salary_text": "35K-45K", "description": "Java 岗位",
        "source_status": "OPEN",
    }
    parent_job_id = client.post("/api/v1/jobs/import", json=job_payload).json()["data"]["job"]["id"]
    request = BrowserReadRequest(platform="BOSS", cdp_url="http://127.0.0.1:9222")
    job_result = ReadResult(
        platform=Platform.BOSS, status=SessionStatus.SESSION_READY, page_type=PageType.JOB,
        page_url="https://www.zhipin.com/job/readonly?token=secret", page_title="职位页",
        content_hash="a" * 64, selector_version="fixture-v1",
        job=BrowserJob(external_job_id="boss-job-1", title="高级Java后端",
                       company_name="BOSS测试公司", industry="互联网", location="北京",
                       work_mode="REMOTE", salary_text="40K-50K", description="Java 岗位描述"),
    )
    session_override = app.dependency_overrides[get_session]
    session_dependency = session_override()
    session = next(session_dependency)
    try:
        first = persist_read_result(session, request, job_result)
        duplicate = persist_read_result(session, request, job_result)
        assert first.imported_job_id is not None
        assert duplicate.id == first.id and duplicate.duplicate

        conversation_result = ReadResult(
            platform=Platform.BOSS, status=SessionStatus.SESSION_READY,
            page_type=PageType.CONVERSATION,
            page_url="https://www.zhipin.com/chat?token=secret", page_title="对话页",
            content_hash="b" * 64, selector_version="fixture-v1",
            conversation=BrowserConversation(
                external_conversation_id="boss-chat-1", recruiter_name="张HR",
                messages=[BrowserMessage(external_message_id="boss-message-1",
                                         content="请发简历", received_at=datetime.now(UTC))],
            ),
        )
        conversation_request = BrowserReadRequest(
            platform="BOSS", cdp_url="http://127.0.0.1:9222", job_id=parent_job_id,
        )
        imported = persist_read_result(session, conversation_request, conversation_result)
        assert imported.imported_conversation_id is not None
        assert len(imported.imported_message_ids) == 1
    finally:
        session.close()
