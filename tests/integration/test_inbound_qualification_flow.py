import os
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.browser.message_discovery import (
    DiscoveredConversation,
    MessageDiscoveryBatch,
)
from adapters.llm.errors import LlmConfigurationError, LlmRateLimitError
from apps.api.app.core.config import Settings
from apps.api.app.core.database import Base
from apps.api.app.models import entities as db
from apps.api.app.schemas.automation import AutomationDispatchRequest
from apps.api.app.schemas.conversation import MessagePayload
from apps.api.app.services.automation_service import dispatch
from apps.api.app.services.conversation_service import (
    create_reply_draft,
    create_resume_draft,
    import_message,
)
from apps.api.app.services.llm_config_service import (
    llm_configuration,
    runtime_settings,
    select_llm_configuration,
)
from apps.api.app.services.message_discovery_service import persist_discovery_batch
from apps.api.app.services.scheduling_service import analyze_invitation
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserMessage,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


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


def test_llm_selection_is_stored_without_api_key_and_applies_immediately(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_providers="ZHIPU,QWEN",
        zhipu_model="glm-test",
        qwen_model="qwen-test",
        zhipu_api_key=SecretStr("zhipu-secret"),
        qwen_api_key=SecretStr("qwen-secret"),
    )
    monkeypatch.setattr(
        "apps.api.app.services.llm_config_service.get_settings",
        lambda: settings,
    )

    selected = select_llm_configuration(session, "QWEN", "qwen-test")

    assert selected["provider"] == "QWEN"
    assert selected["model"] == "qwen-test"
    assert runtime_settings(session, settings).llm_provider == "QWEN"
    assert "secret" not in str(llm_configuration(session))


def test_explicit_inbound_resume_request_does_not_require_score(
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
        name="Java策略",
        enabled=True,
        priority=1,
    )
    strategy.title_rules.append(
        db.JobTitleRule(
            rule_type="INCLUDE",
            pattern="Java后端",
            normalized_pattern="java后端",
            score=15,
        )
    )
    session.add(strategy)
    session.flush()
    job = db.Job(
        user_id=DEFAULT_USER_ID,
        source="MOCK",
        external_job_id="inbound-job",
        content_hash="a" * 64,
        title="Java后端开发",
        company_name="示例科技",
        industry="互联网",
        location="远程",
        work_mode="REMOTE",
        salary_text=None,
        description="负责 Java 服务端研发",
        source_status="OPEN",
        raw_data={},
    )
    session.add(job)
    session.flush()
    conversation = db.Conversation(
        user_id=DEFAULT_USER_ID,
        job_id=job.id,
        strategy_id=strategy.id,
        platform="MOCK",
        external_conversation_id="inbound-conversation",
        recruiter_name="招聘人",
    )
    session.add(conversation)
    primary_resume = db.Resume(
        user_id=DEFAULT_USER_ID,
        platform="MOCK",
        attachment_name="Java后端简历.pdf",
        target_directions=["Java后端"],
        is_available=True,
    )
    other_resume = db.Resume(
        user_id=DEFAULT_USER_ID,
        platform="MOCK",
        attachment_name="其他简历.pdf",
        target_directions=["产品"],
        is_available=True,
    )
    session.add_all([primary_resume, other_resume])
    session.commit()
    message = import_message(
        session,
        conversation.id,
        payload=MessagePayload(
            external_message_id="resume-request",
            content="岗位大体合适，请发一份简历",
            received_at=datetime.now(UTC),
        ),
    )

    draft = create_resume_draft(session, message.id)

    assert conversation.qualification_status == "ROUGH_MATCH"
    assert draft.decision.value == "ALLOW_AUTO"
    assert draft.resume_id is not None
    assert draft.reason_codes == ["INBOUND_RESUME_REQUEST_ALLOWED"]
    assert session.query(db.JobScore).count() == 0
    session.add(
        db.AutomationSetting(
            user_id=DEFAULT_USER_ID,
            scope_type="GLOBAL",
            scope_key="GLOBAL",
            enabled=True,
            auto_resume_enabled=True,
        )
    )
    session.commit()
    with pytest.raises(ValueError, match="草稿策略决策不匹配"):
        dispatch(
            session,
            AutomationDispatchRequest(
                action_type="RESUME",
                conversation_id=conversation.id,
                draft_id=draft.id,
                resume_id=other_resume.id,
            ),
            executor=FakeActionExecutor(),
        )
    sent = dispatch(
        session,
        AutomationDispatchRequest(
            action_type="RESUME",
            conversation_id=conversation.id,
            draft_id=draft.id,
            resume_id=draft.resume_id,
        ),
        executor=FakeActionExecutor(),
    )
    assert sent["action_status"] == "SUCCEEDED"
    action = session.get(db.ActionQueue, sent["action_id"])
    assert action is not None
    assert action.authorization_basis == "INBOUND_EXPLICIT_RESUME_REQUEST"
    assert action.qualification_snapshot["status"] == "ROUGH_MATCH"
    assert action.evidence_message_ids

    phone_message = import_message(
        session,
        conversation.id,
        MessagePayload(
            external_message_id="phone-invitation",
            content="2026-07-25 10:00 可以电话沟通吗，北京时间",
            received_at=datetime.now(UTC),
        ),
    )
    phone_request = analyze_invitation(
        session, phone_message.id, calendar_available=False
    )
    assert phone_request["status"] == "PENDING_APPROVAL"

    job.salary_text = "25K-35K"
    session.commit()
    interview_message = import_message(
        session,
        conversation.id,
        MessagePayload(
            external_message_id="interview-invitation",
            content="2026-07-26 14:00 可以视频面试吗，北京时间",
            received_at=datetime.now(UTC),
        ),
    )
    interview_request = analyze_invitation(
        session, interview_message.id, calendar_available=False
    )
    assert conversation.qualification_status == "FULL_MATCH"
    assert interview_request["status"] == "PENDING_APPROVAL"

    strategy.arrival_time_reply = (
        "我最快可以在一周内到岗，具体日期可以结合双方安排确认。"
    )
    strategy.version += 1
    session.commit()
    arrival_message = import_message(
        session,
        conversation.id,
        MessagePayload(
            external_message_id="arrival-date-question",
            content="最快到岗时间是多久？",
            received_at=datetime.now(UTC),
        ),
    )

    llm_provider = Mock()
    llm_provider.classify_message.side_effect = LlmRateLimitError("限流")
    arrival_draft = create_reply_draft(session, arrival_message.id, llm_provider)

    assert arrival_draft.content == strategy.arrival_time_reply
    assert arrival_draft.decision.value == "ALLOW_AUTO"
    assert arrival_draft.reply_source.value == "RULE_TEMPLATE"
    llm_provider.classify_message.assert_not_called()
    assert arrival_draft.reason_codes == ["CONFIGURED_ARRIVAL_TIME_REPLY"]


def test_unbound_inbound_message_gets_safe_clarification(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_provider(_: object) -> None:
        raise LlmConfigurationError("测试模型未配置")

    monkeypatch.setattr(
        "apps.api.app.services.conversation_service.build_llm_provider",
        unavailable_provider,
    )
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
        name="默认策略",
        enabled=True,
        priority=1,
    )
    session.add(strategy)
    session.flush()
    conversation = db.Conversation(
        user_id=DEFAULT_USER_ID,
        job_id=None,
        strategy_id=strategy.id,
        platform="MOCK",
        external_conversation_id="unknown-conversation",
        recruiter_name="招聘人",
    )
    session.add(conversation)
    session.commit()
    message = import_message(
        session,
        conversation.id,
        MessagePayload(
            external_message_id="unknown-message",
            content="您好，在看新的机会吗？",
            received_at=datetime.now(UTC),
        ),
    )

    draft = create_reply_draft(session, message.id)

    assert conversation.qualification_status == "UNKNOWN"
    assert draft.decision.value == "ALLOW_AUTO"
    assert draft.reason_codes == ["SAFE_JOB_CLARIFICATION"]
    assert "岗位方向" in draft.content

    time_message = import_message(
        session,
        conversation.id,
        MessagePayload(
            external_message_id="unknown-phone",
            content="2026-07-25 10:00 可以电话沟通吗，北京时间",
            received_at=datetime.now(UTC),
        ),
    )
    with pytest.raises(ValueError, match="大致匹配"):
        analyze_invitation(session, time_message.id, calendar_available=False)

    related_message = import_message(
        session,
        conversation.id,
        MessagePayload(
            external_message_id="related-message",
            content="这是一个 Java后端开发岗位，方便了解一下吗？",
            received_at=datetime.now(UTC),
        ),
    )
    related_draft = create_reply_draft(session, related_message.id)
    assert conversation.qualification_status == "ROUGH_MATCH"
    assert related_draft.decision.value == "ALLOW_AUTO"
    assert related_draft.reason_codes == ["SAFE_JOB_DETAIL_CLARIFICATION"]


@pytest.mark.parametrize(
    ("platform", "page_url"),
    [
        (Platform.BOSS, "https://www.zhipin.com/web/geek/chat"),
        (Platform.MAIMAI, "https://maimai.cn/chat"),
    ],
)
def test_message_discovery_imports_unbound_scoreless_conversation(
    session: Session,
    platform: Platform,
    page_url: str,
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
        name="默认策略",
        enabled=True,
        priority=1,
    )
    session.add(strategy)
    session.flush()
    run = db.AgentRun(
        user_id=DEFAULT_USER_ID,
        strategy_id=strategy.id,
        platform=platform.value,
        executor_type="REAL_CDP",
        status="RUNNING",
        cursor={},
    )
    session.add(run)
    session.flush()
    now = datetime.now(UTC)
    conversation_key = f"{platform.value.lower()}-unbound-chat"
    message_key = f"{platform.value.lower()}-unbound-message"
    counts = persist_discovery_batch(
        session,
        run,
        "test-worker",
        MessageDiscoveryBatch(
            platform=platform,
            partition="UNREAD",
            scroll_position=1,
            scanned_at=now,
            exhausted=True,
            items=[
                DiscoveredConversation(
                    summary={
                        "external_conversation_id": conversation_key,
                        "recruiter_name": "招聘人",
                        "job_title": "Java后端",
                        "company_name": "观察公司",
                        "last_message_id": message_key,
                        "unread_count": 1,
                    },
                    detail=ReadResult(
                        platform=platform,
                        status=SessionStatus.SESSION_READY,
                        page_type=PageType.CONVERSATION,
                        page_url=page_url,
                        page_title="消息",
                        content_hash="a" * 64,
                        selector_version="fixture",
                        conversation=BrowserConversation(
                            external_conversation_id=conversation_key,
                            recruiter_name="招聘人",
                            job_title="Java后端",
                            company_name="观察公司",
                            messages=[
                                BrowserMessage(
                                    external_message_id=message_key,
                                    content="您好，可以发一份简历吗？",
                                    received_at=now,
                                )
                            ],
                        ),
                    ),
                )
            ],
        ),
    )

    conversation = session.scalar(
        select(db.Conversation).where(
            db.Conversation.external_conversation_id == conversation_key
        )
    )
    assert counts == {
        "discovered": 1,
        "imported": 1,
        "paused": 0,
        "skipped": 0,
    }
    assert conversation is not None
    assert conversation.job_id is None
    assert conversation.latest_job_score_id is None
    assert conversation.state == "ACTIVE"
    assert conversation.observed_company_name == "观察公司"
    assert conversation.observed_job_title == "Java后端"
