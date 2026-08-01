import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.job_discovery import DiscoveredJob, JobDiscoveryBatch
from adapters.browser.message_discovery import (
    DiscoveredConversation,
    MessageDiscoveryBatch,
)
from adapters.llm.errors import LlmServiceError
from adapters.llm.fake import FakeLlmProvider
from apps.api.app.core.config import Settings
from apps.api.app.core.database import Base, get_session
from apps.api.app.main import app
from apps.api.app.models import entities  # noqa: F401
from apps.api.app.schemas.browser import BrowserReadRequest
from apps.api.app.services.agent_service import tick_run
from apps.api.app.services.browser_service import persist_read_result
from apps.api.app.services.job_discovery_service import process_job_discovery_batch
from apps.api.app.services.message_discovery_service import persist_discovery_batch
from apps.api.app.services.operations_service import (
    apply_retention,
    enqueue_unknown_actions,
    heartbeat_worker,
    process_reconciliation_queue,
    register_worker,
    stop_worker,
    verify_successful_actions,
)
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
from packages.llm.models import LlmResult, MessageClassification, MessageClassificationRequest


class FailingLlmProvider(FakeLlmProvider):
    def classify_message(
        self, request: MessageClassificationRequest
    ) -> LlmResult[MessageClassification]:
        raise LlmServiceError("测试模型故障")


class CountingLlmProvider(FakeLlmProvider):
    def __init__(self) -> None:
        self.parse_calls = 0
        self.score_calls = 0
        self.greeting_calls = 0

    def parse_job(self, request: object):  # type: ignore[no-untyped-def, override]
        self.parse_calls += 1
        return super().parse_job(request)  # type: ignore[arg-type]

    def score_job(self, request: object):  # type: ignore[no-untyped-def, override]
        self.score_calls += 1
        return super().score_job(request)  # type: ignore[arg-type]

    def generate_greeting(self, request: object):  # type: ignore[no-untyped-def, override]
        self.greeting_calls += 1
        return super().generate_greeting(request)  # type: ignore[arg-type]


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
    mock_settings = Settings(_env_file=None, calendar_provider="MOCK")
    monkeypatch.setattr(
        "apps.api.app.api.v1.scheduling.get_settings", lambda: mock_settings
    )
    monkeypatch.setattr(
        "apps.api.app.services.agent_service.build_calendar_gateway", lambda _: None
    )
    monkeypatch.setattr(
        "apps.api.app.services.score_service.build_runtime_llm_provider",
        lambda _: FakeLlmProvider(),
    )
    monkeypatch.setattr(
        "apps.api.app.services.conversation_service.build_runtime_llm_provider",
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
        "company_blacklist": [], "accept_outsourcing": False, "accept_part_time": True,
        "accept_headhunter": True,
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
        "source": "BOSS",
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
    assert all(
        reference.startswith("evidence:")
        for detail in first_score.json()["data"]["details"]
        for reference in detail["evidence_refs"]
    )
    assert all(
        detail["matched_facts"]["evidence_items"]
        for detail in first_score.json()["data"]["details"]
    )
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
    assert greeting.json()["data"]["fact_ids"]
    assert greeting.json()["data"]["confirmation_task_id"] is None

    conversation = client.post("/api/v1/conversations", json={
        "job_id": job_id, "external_conversation_id": "integration-conversation-1",
        "recruiter_name": "集成测试招聘人", "platform": "MOCK",
    })
    conversation_id = conversation.json()["data"]["id"]
    old_greeting = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-old-greeting", "content": "您好，在看机会吗？",
        "received_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }).json()["data"]
    technical_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-1", "content": "请介绍 Java 技术栈经验",
        "received_at": datetime.now(UTC).isoformat(),
    })
    message_id = technical_message.json()["data"]["id"]
    superseded_reply = client.post(
        "/api/v1/drafts/reply", json={"message_id": old_greeting["id"]}
    )
    assert superseded_reply.status_code == 400
    reply = client.post("/api/v1/drafts/reply", json={"message_id": message_id})
    duplicate_reply = client.post("/api/v1/drafts/reply", json={"message_id": message_id})
    assert reply.json()["data"]["decision"] == "ALLOW_AUTO"
    assert duplicate_reply.json()["data"]["id"] == reply.json()["data"]["id"]
    assert reply.json()["data"]["confirmation_task_id"] is None
    edited_reply = client.patch(
        f"/api/v1/drafts/{reply.json()['data']['id']}",
        json={"content": "您好，我有 Java 后端开发经验，请问该岗位主要负责哪些系统？"},
    )
    assert edited_reply.status_code == 200
    assert edited_reply.json()["data"]["id"] != reply.json()["data"]["id"]
    assert edited_reply.json()["data"]["reason_codes"] == ["USER_EDIT_REVALIDATED"]

    resume_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-resume", "content": "岗位很合适，请发一份简历",
        "received_at": datetime.now(UTC).isoformat(),
    }).json()["data"]
    resume_draft = client.post(
        "/api/v1/drafts/resume", json={"message_id": resume_message["id"]}
    )
    assert resume_draft.json()["data"]["decision"] == "ALLOW_AUTO"
    assert resume_draft.json()["data"]["resume_id"] == resume.json()["data"]["id"]
    assert resume_draft.json()["data"]["confirmation_task_id"] is None

    low_job = client.post("/api/v1/jobs/import", json={
        "external_job_id": "integration-low-score",
        "title": "线下销售",
        "company_name": "低匹配测试公司",
        "industry": "零售",
        "location": "北京",
        "work_mode": "ONSITE",
        "description": "负责门店销售工作",
        "source_status": "OPEN",
    }).json()["data"]["job"]
    low_conversation = client.post("/api/v1/conversations", json={
        "job_id": low_job["id"],
        "external_conversation_id": "integration-low-conversation",
        "recruiter_name": "低匹配招聘人",
        "platform": "MOCK",
    }).json()["data"]
    low_message = client.post(
        f"/api/v1/conversations/{low_conversation['id']}/messages",
        json={
            "external_message_id": "low-message-1",
            "content": "你好，是否考虑这个岗位？",
            "received_at": datetime.now(UTC).isoformat(),
        },
    ).json()["data"]
    low_decline = client.post("/api/v1/drafts/reply", json={"message_id": low_message["id"]})
    repeated_decline = client.post("/api/v1/drafts/reply", json={"message_id": low_message["id"]})
    assert low_decline.json()["data"]["draft_type"] == "MISMATCH_DECLINE"
    assert low_decline.json()["data"]["decision"] == "ALLOW_AUTO"
    assert "黑名单" not in low_decline.json()["data"]["content"]
    assert repeated_decline.json()["data"]["id"] == low_decline.json()["data"]["id"]

    automation_setting = {
        "scope_type": "GLOBAL", "scope_key": "GLOBAL", "enabled": False,
        "paused": False, "auto_greet_enabled": True, "auto_greet_min_score": 80,
        "auto_reply_enabled": True, "auto_resume_enabled": True,
            "job_scan_enabled": True, "company_cooldown_hours": 24,
        "recruiter_cooldown_hours": 24, "work_start_hour": 8,
        "work_end_hour": 22, "emergency_stop": False,
    }
    saved_setting = client.put("/api/v1/automation/settings", json=automation_setting)
    assert saved_setting.status_code == 200
    assert client.put("/api/v1/automation/settings", json={
        "scope_type": "GLOBAL", "scope_key": "GLOBAL", "enabled": False,
    }).status_code == 200
    persisted_setting = client.get("/api/v1/automation/settings").json()["data"]["items"][0]
    assert persisted_setting["enabled"] is False
    assert persisted_setting["auto_reply_enabled"] is True
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
    decline_sent = client.post("/api/v1/automation/dispatch", json={
        "action_type": "MISMATCH_DECLINE",
        "conversation_id": low_conversation["id"],
        "draft_id": low_decline.json()["data"]["id"],
        "cdp_url": "http://127.0.0.1:9222",
    })
    assert decline_sent.json()["data"]["action_status"] == "SUCCEEDED"
    declined_conversation = next(
        item
        for item in client.get("/api/v1/conversations").json()["data"]["items"]
        if item["id"] == low_conversation["id"]
    )
    assert declined_conversation["state"] == "DECLINED"

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
    assert len(tasks.json()["data"]["items"]) == 1
    confirmation_metrics = client.get("/api/v1/automation/operations/status").json()["data"]
    assert confirmation_metrics["pending_human_confirmation_count"] == 1
    assert confirmation_metrics["pending_schedule_confirmation_count"] == 0

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
    schedule_metrics = client.get("/api/v1/automation/operations/status").json()["data"]
    assert schedule_metrics["pending_schedule_confirmation_count"] == 1

    explicit_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-schedule-explicit",
        "content": "2026-07-24 10:00 可以电话沟通吗，北京时间",
        "received_at": datetime.now(UTC).isoformat(),
    }).json()["data"]
    schedule = client.post("/api/v1/scheduling/analyze", json={
        "message_id": explicit_message["id"], "calendar_available": True,
    }).json()["data"]
    assert schedule["calendar_status"] == "AVAILABLE"
    assert schedule["company_name"] == job_payload["company_name"]
    assert schedule["job_title"] == job_payload["title"]
    assert schedule["recruiter_name"] == "集成测试招聘人"
    assert schedule["qualification_status"] == "FULL_MATCH"
    prior_schedule = next(
        item
        for item in client.get("/api/v1/scheduling/requests").json()["data"]["items"]
        if item["id"] == unavailable_schedule.json()["data"]["id"]
    )
    assert prior_schedule["status"] == "SUPERSEDED"
    unsafe_schedule = client.post(f"/api/v1/scheduling/requests/{schedule['id']}/approve", json={
        "reply_content": "我的身份证号是 370000000000000000", "create_calendar_event": False,
    })
    assert unsafe_schedule.status_code == 400
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
    selected_slot = (
        changed_schedule["candidate_slots"][0]
        if changed_schedule["candidate_slots"]
        else {
            "start_at": changed_schedule["start_at"],
            "end_at": changed_schedule["end_at"],
        }
    )
    selected_start = datetime.fromisoformat(selected_slot["start_at"])
    selected_end = datetime.fromisoformat(selected_slot["end_at"])
    changed_approval = client.post(f"/api/v1/scheduling/requests/{changed_schedule['id']}/approve", json={
        "reply_content": changed_schedule["suggested_reply"],
        "selected_start_at": selected_slot["start_at"],
        "selected_end_at": selected_slot["end_at"],
        "create_calendar_event": False,
    })
    assert changed_approval.status_code == 200
    client.post("/api/v1/scheduling/calendar-events", json={
        "external_event_id": "new-conflict", "title": "新增忙碌",
        "start_at": (selected_start - timedelta(minutes=5)).isoformat(),
        "end_at": (selected_end + timedelta(minutes=5)).isoformat(),
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

    rejected_message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "external_message_id": "message-schedule-rejected",
        "content": "2026-07-25 10:00 可以电话沟通吗，北京时间",
        "received_at": datetime.now(UTC).isoformat(),
    }).json()["data"]
    rejected_schedule = client.post("/api/v1/scheduling/analyze", json={
        "message_id": rejected_message["id"], "calendar_available": True,
    }).json()["data"]
    rejected_result = client.post(
        f"/api/v1/scheduling/requests/{rejected_schedule['id']}/reject"
    )
    assert rejected_result.json()["data"]["status"] == "CANCELLED"

    monkeypatch.setattr(
        "apps.api.app.services.agent_service.build_runtime_llm_provider",
        lambda *_: FakeLlmProvider(),
    )
    started_run = client.post("/api/v1/automation/runs", json={
        "platform": "MOCK", "strategy_id": strategy_id,
    })
    assert started_run.status_code == 200
    run_id = started_run.json()["data"]["id"]
    lease_engine = create_engine(os.environ["TEST_DATABASE_URL"])
    discovery_run = client.post("/api/v1/automation/runs", json={
        "platform": "BOSS", "strategy_id": strategy_id,
    })
    assert discovery_run.status_code == 200
    discovery_run_id = discovery_run.json()["data"]["id"]
    discovered_items = []
    for index in range(100):
        discovered_items.append(DiscoveredConversation(
            summary={
                "external_conversation_id": f"discovery-chat-{index}",
                "recruiter_name": f"招聘人-{index}",
                "job_title": job_payload["title"],
                "company_name": job_payload["company_name"],
                "external_job_id": job_payload["external_job_id"],
                "last_message_id": f"discovery-message-{index}",
                "unread_count": 1,
            },
            detail=ReadResult(
                platform=Platform.BOSS,
                status=SessionStatus.SESSION_READY,
                page_type=PageType.CONVERSATION,
                page_url="https://www.zhipin.com/web/geek/chat",
                page_title="消息",
                content_hash=f"{index:064d}"[-64:],
                selector_version="fixture",
                conversation=BrowserConversation(
                    external_conversation_id=f"discovery-chat-{index}",
                    recruiter_name=f"招聘人-{index}",
                    job_title=job_payload["title"],
                    company_name=job_payload["company_name"],
                    external_job_id=job_payload["external_job_id"],
                    messages=[BrowserMessage(
                        external_message_id=f"discovery-message-{index}",
                        content="您好，在看新的工作机会吗？",
                        received_at=datetime.now(UTC),
                    )],
                ),
            ),
        ))
    with Session(lease_engine, expire_on_commit=False) as discovery_session:
        run_entity = discovery_session.get(entities.AgentRun, discovery_run_id)
        assert run_entity is not None
        counts = persist_discovery_batch(
            discovery_session,
            run_entity,
            "discovery-worker",
            MessageDiscoveryBatch(
                platform=Platform.BOSS,
                partition="UNREAD",
                scroll_position=100,
                scanned_at=datetime.now(UTC),
                items=discovered_items,
                seen_message_keys=[
                    f"discovery-chat-{index}:discovery-message-{index}"
                    for index in range(100)
                ],
                exhausted=True,
            ),
        )
        assert counts == {
            "discovered": 100,
            "imported": 100,
            "paused": 0,
            "skipped": 0,
        }
        message_cursor = run_entity.cursor["message_discovery"]
        assert isinstance(message_cursor, dict)
        assert message_cursor["scroll_position"] == 0
        assert discovery_session.scalar(
            select(entities.Message)
            .join(entities.Conversation)
            .where(entities.Conversation.platform == "BOSS")
            .limit(1)
        ) is not None
    unsafe_boss_tick = client.post(
        f"/api/v1/automation/runs/{discovery_run_id}/tick",
        json={"worker_id": "api-without-real-executor"},
    )
    assert unsafe_boss_tick.status_code == 400
    proactive_now = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)
    proactive_summary = BrowserJob(
        external_job_id="proactive-job-1",
        title="Java后端工程师",
        company_name="主动发现测试公司",
        industry="互联网",
        location="远程",
        work_mode="REMOTE",
        salary_text="40K-45K",
        recruiter_name="主动发现招聘人",
        description="5年以上Java、Spring Boot和MySQL经验",
        source_status="OPEN",
    )
    with Session(lease_engine, expire_on_commit=False) as discovery_session:
        run_entity = discovery_session.get(entities.AgentRun, discovery_run_id)
        assert run_entity is not None
        proactive_counts = process_job_discovery_batch(
            discovery_session,
            run_entity,
            JobDiscoveryBatch(
                platform=Platform.BOSS,
                search_key="java",
                scroll_position=1,
                scanned_at=proactive_now,
                next_scan_at=proactive_now + timedelta(seconds=30),
                seen_job_ids=["proactive-job-1"],
                exhausted=True,
                items=[DiscoveredJob(
                    summary={
                        "external_job_id": "proactive-job-1",
                        "title": "Java后端工程师",
                        "company_name": "主动发现测试公司",
                    },
                    detail=ReadResult(
                        platform=Platform.BOSS,
                        status=SessionStatus.SESSION_READY,
                        page_type=PageType.JOB,
                        page_url="https://www.zhipin.com/job_detail/proactive-job-1.html",
                        page_title="Java后端工程师",
                        content_hash="c" * 64,
                        selector_version="fixture",
                        job=proactive_summary,
                    ),
                )],
            ),
            provider=FakeLlmProvider(),
            executor=FakeActionExecutor(),
            cdp_url="http://127.0.0.1:9222",
            now=proactive_now,
        )
        assert proactive_counts == {
            "discovered": 1,
            "scored": 1,
            "contacted": 1,
            "skipped": 0,
        }
        repeated_counts = process_job_discovery_batch(
            discovery_session,
            run_entity,
            JobDiscoveryBatch(
                platform=Platform.BOSS,
                search_key="java",
                scroll_position=1,
                scanned_at=proactive_now + timedelta(minutes=1),
                next_scan_at=proactive_now + timedelta(minutes=2),
                seen_job_ids=["proactive-job-1"],
                exhausted=True,
                items=[DiscoveredJob(
                    summary={
                        "external_job_id": "proactive-job-1",
                        "title": "Java后端工程师",
                        "company_name": "主动发现测试公司",
                    },
                    detail=ReadResult(
                        platform=Platform.BOSS,
                        status=SessionStatus.SESSION_READY,
                        page_type=PageType.JOB,
                        page_url="https://www.zhipin.com/job_detail/proactive-job-1.html",
                        page_title="Java后端工程师",
                        content_hash="c" * 64,
                        selector_version="fixture",
                        job=proactive_summary,
                    ),
                )],
            ),
            provider=FakeLlmProvider(),
            executor=FakeActionExecutor(),
            cdp_url="http://127.0.0.1:9222",
            now=proactive_now + timedelta(minutes=2),
        )
        assert repeated_counts["contacted"] == 0
        hard_filtered_provider = CountingLlmProvider()
        hard_filtered = BrowserJob(
            external_job_id="hard-filtered-onsite-job",
            title="Java后端工程师",
            company_name="硬性排除测试公司",
            industry="互联网",
            location="北京",
            work_mode="ONSITE",
            salary_text="40K-45K",
            recruiter_name="硬性排除测试招聘人",
            description="5年以上Java、Spring Boot和MySQL经验",
            source_status="OPEN",
        )
        hard_filtered_counts = process_job_discovery_batch(
            discovery_session,
            run_entity,
            JobDiscoveryBatch(
                platform=Platform.BOSS,
                search_key="java",
                scroll_position=2,
                scanned_at=proactive_now + timedelta(minutes=3),
                next_scan_at=proactive_now + timedelta(minutes=4),
                seen_job_ids=["hard-filtered-onsite-job"],
                exhausted=True,
                items=[
                    DiscoveredJob(
                        summary={
                            "external_job_id": "hard-filtered-onsite-job",
                            "title": "Java后端工程师",
                            "company_name": "硬性排除测试公司",
                        },
                        detail=ReadResult(
                            platform=Platform.BOSS,
                            status=SessionStatus.SESSION_READY,
                            page_type=PageType.JOB,
                            page_url=(
                                "https://www.zhipin.com/job_detail/"
                                "hard-filtered-onsite-job.html"
                            ),
                            page_title="Java后端工程师",
                            content_hash="e" * 64,
                            selector_version="fixture",
                            job=hard_filtered,
                        ),
                    )
                ],
            ),
            provider=hard_filtered_provider,
            executor=FakeActionExecutor(),
            cdp_url="http://127.0.0.1:9222",
            now=proactive_now + timedelta(minutes=3),
        )
        assert hard_filtered_counts == {
            "discovered": 1,
            "scored": 0,
            "contacted": 0,
            "skipped": 1,
        }
        assert hard_filtered_provider.parse_calls == 0
        assert hard_filtered_provider.score_calls == 0
        assert hard_filtered_provider.greeting_calls == 0
    duplicate_start = client.post("/api/v1/automation/runs", json={
        "platform": "MOCK", "strategy_id": strategy_id,
    })
    assert duplicate_start.json()["data"]["id"] == run_id

    with Session(lease_engine) as lease_session:
        run_entity = lease_session.get(entities.AgentRun, run_id)
        assert run_entity is not None
        run_entity.lease_owner = "dead-worker"
        run_entity.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        lease_session.commit()
    first_tick = client.post(
        f"/api/v1/automation/runs/{run_id}/tick",
        json={"worker_id": "worker-1"},
    )
    assert first_tick.status_code == 200
    first_action_count = first_tick.json()["data"]["action_count"]
    assert first_action_count >= 1

    with Session(lease_engine) as lease_session:
        run_entity = lease_session.get(entities.AgentRun, run_id)
        assert run_entity is not None
        run_entity.lease_owner = "worker-other"
        run_entity.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        lease_session.commit()
    blocked_lease = client.post(
        f"/api/v1/automation/runs/{run_id}/tick",
        json={"worker_id": "worker-2"},
    )
    assert blocked_lease.status_code == 400

    paused_run = client.post(f"/api/v1/automation/runs/{run_id}/pause")
    assert paused_run.json()["data"]["status"] == "PAUSED"
    assert client.post(
        f"/api/v1/automation/runs/{run_id}/tick",
        json={"worker_id": "worker-1"},
    ).status_code == 400
    resumed_run = client.post(f"/api/v1/automation/runs/{run_id}/resume")
    assert resumed_run.json()["data"]["status"] == "RUNNING"
    second_tick = client.post(
        f"/api/v1/automation/runs/{run_id}/tick",
        json={"worker_id": "worker-2"},
    )
    assert second_tick.status_code == 200
    assert second_tick.json()["data"]["action_count"] == first_action_count
    with Session(lease_engine) as lease_session:
        events = lease_session.scalars(
            select(entities.AgentRunEvent).where(
                entities.AgentRunEvent.agent_run_id == run_id
            )
        ).all()
        actions = lease_session.scalars(
            select(entities.ActionQueue).where(entities.ActionQueue.agent_run_id == run_id)
        ).all()
        assert events
        assert len({action.send_fingerprint for action in actions}) == len(actions)

    failing_message = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "external_message_id": "agent-llm-failure",
            "content": "请介绍一个尚未处理的问题",
            "received_at": datetime.now(UTC).isoformat(),
        },
    )
    assert failing_message.status_code == 200
    for attempt in range(3):
        with Session(lease_engine, expire_on_commit=False) as agent_session:
            failed_tick = tick_run(
                agent_session,
                run_id,
                f"failure-worker-{attempt}",
                provider=FailingLlmProvider(),
                executor=FakeActionExecutor(),
            )
    assert failed_tick["status"] == "RUNNING"
    with Session(lease_engine) as waiting_session:
        waiting_message = waiting_session.scalar(
            select(entities.Message).where(
                entities.Message.external_message_id == "agent-llm-failure"
            )
        )
        assert waiting_message is not None
        assert waiting_message.status == "WAITING_FOR_LLM"
        assert waiting_session.scalar(
            select(entities.GeneratedDraft.id).where(
                entities.GeneratedDraft.message_id == waiting_message.id
            )
        ) is None

    deferred_message = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "external_message_id": "agent-llm-circuit-open",
            "content": "请再介绍一个尚未处理的问题",
            "received_at": datetime.now(UTC).isoformat(),
        },
    )
    assert deferred_message.status_code == 200
    with Session(lease_engine, expire_on_commit=False) as agent_session:
        tick_run(
            agent_session,
            run_id,
            "circuit-open-worker",
            executor=FakeActionExecutor(),
        )
    with Session(lease_engine) as waiting_session:
        circuit = waiting_session.scalar(select(entities.LlmCircuitBreaker))
        deferred = waiting_session.scalar(
            select(entities.Message).where(
                entities.Message.external_message_id == "agent-llm-circuit-open"
            )
        )
        assert circuit is not None
        assert circuit.failure_code == "LLM_SERVICE_ERROR"
        assert deferred is not None
        assert deferred.status == "WAITING_FOR_LLM"
        assert deferred.error_code == "LLM_SERVICE_ERROR"

    safety_conversation = client.post("/api/v1/conversations", json={
        "job_id": job_id,
        "external_conversation_id": "agent-safety-conversation",
        "recruiter_name": "安全异常招聘人",
        "platform": "MOCK",
    }).json()["data"]
    safety_message = client.post(
        f"/api/v1/conversations/{safety_conversation['id']}/messages",
        json={
            "external_message_id": "agent-safety-failure",
            "content": "请继续介绍 Java 技术栈",
            "received_at": datetime.now(UTC).isoformat(),
        },
    )
    assert safety_message.status_code == 200
    with Session(lease_engine, expire_on_commit=False) as agent_session:
        safety_tick = tick_run(
            agent_session,
            run_id,
            "safety-worker",
            provider=FakeLlmProvider(),
            executor=FakeActionExecutor(
                [
                    ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="RESULT_NOT_OBSERVED",
                    )
                ]
            ),
        )
    assert safety_tick["status"] == "PAUSED"
    assert safety_tick["pause_reason_codes"] == ["RESULT_NOT_OBSERVED"]
    run_items = client.get("/api/v1/automation/runs").json()["data"]["items"]
    assert any(item["id"] == run_id and item["status"] == "PAUSED" for item in run_items)
    automatic_actions = client.get("/api/v1/automation/actions").json()["data"]["items"]
    assert automatic_actions
    with Session(lease_engine, expire_on_commit=False) as operations_session:
        registered = register_worker(
            operations_session,
            "integration-worker-1",
            "localhost",
            1001,
        )
        heartbeat_worker(operations_session, registered.worker_id)
        with pytest.raises(RuntimeError, match="已有健康"):
            register_worker(
                operations_session,
                "integration-worker-2",
                "localhost",
                1002,
            )
        registered.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
        operations_session.commit()
        replacement = register_worker(
            operations_session,
            "integration-worker-2",
            "localhost",
            1002,
        )
        operations_session.refresh(registered)
        assert registered.status == "STALE"
        stop_worker(operations_session, replacement.worker_id)
        assert enqueue_unknown_actions(operations_session) == 1
        task = operations_session.scalar(
            select(entities.ReconciliationTask)
        )
        assert task is not None and task.status == "PENDING"
        task.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        operations_session.commit()

        class UnknownObserver:
            def observe(self, *_: object) -> ExecutionResult:
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="RESULT_NOT_OBSERVED",
                )

        reconciliation = process_reconciliation_queue(
            operations_session,
            "http://127.0.0.1:9222",
            observer=UnknownObserver(),  # type: ignore[arg-type]
        )
        assert reconciliation["manual_required"] == 1
        assert task.status == "MANUAL_REQUIRED"

        class MissingObserver:
            def observe(self, *_: object) -> ExecutionResult:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="RESULT_CONFIRMED_NOT_SENT",
                )

        discrepancies = verify_successful_actions(
            operations_session,
            "http://127.0.0.1:9222",
            observer=MissingObserver(),  # type: ignore[arg-type]
            limit=1,
        )
        assert discrepancies[0]["code"] == "PLATFORM_MISSING_DATABASE_SUCCESS"
        old_event = entities.AgentRunEvent(
            agent_run_id=run_id,
            event_type="RETENTION_FIXTURE",
            reason_codes=[],
            metadata_json={},
            created_at=datetime.now(UTC) - timedelta(days=1000),
        )
        operations_session.add(old_event)
        operations_session.commit()
        retention = apply_retention(operations_session)
        assert retention["run_events_deleted"] >= 1
    operation_status = client.get("/api/v1/automation/operations/status")
    assert operation_status.status_code == 200
    assert operation_status.json()["data"]["unknown_action_count"] >= 1
    conversation_items = client.get("/api/v1/conversations").json()["data"]["items"]
    assert any(
        item["id"] == conversation_id
        and item["strategy_id"] == strategy_id
        and item["latest_score"] is not None
        for item in conversation_items
    )
    lease_engine.dispose()


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
