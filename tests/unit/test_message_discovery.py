import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.request import Request
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from adapters.browser.message_discovery import (
    BossMessageDiscoveryAdapter,
    DiscoveredConversation,
    MessageDiscoveryAdapter,
    MessageDiscoveryBatch,
    _attempted_message_keys,
    _job_id_from_href,
    _matches_partition,
    _message_key,
    _normalize_duplicate_conversation_ids,
    _verify_target,
    select_discovery_candidates,
)
from adapters.llm.fake import FakeLlmProvider
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import ConversationPayload
from apps.api.app.services.conversation_service import create_conversation
from apps.api.app.services.message_discovery_service import (
    _location_consent_allowed,
    _next_seen_message_keys,
    _terminal_state_from_messages,
    persist_discovery_batch,
    process_next_inbound_job_decision,
    record_ready_platform_session,
)
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserConversationSummary,
    BrowserMessage,
    MessageDirection,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


def summary(index: int, *, message: int | None = None) -> BrowserConversationSummary:
    return BrowserConversationSummary(
        external_conversation_id=f"conversation-{index}",
        recruiter_name=f"招聘人-{index}",
        job_title="Java 后端",
        company_name="测试公司",
        external_job_id="job-1",
        last_message_id=f"message-{message if message is not None else index}",
        unread_count=1,
    )


def detail(item: BrowserConversationSummary) -> ReadResult:
    return ReadResult(
        platform=Platform.BOSS,
        status=SessionStatus.SESSION_READY,
        page_type=PageType.CONVERSATION,
        page_url="https://www.zhipin.com/web/geek/chat",
        page_title="消息",
        content_hash="a" * 64,
        selector_version="fixture",
        conversation=BrowserConversation(
            external_conversation_id=item.external_conversation_id,
            recruiter_name=item.recruiter_name,
            job_title=item.job_title,
            company_name=item.company_name,
            external_job_id=item.external_job_id,
        ),
)


def test_linked_job_close_does_not_close_message_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_open(url: str, timeout: int) -> object:
        calls.append(url)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            [
                {
                    "id": "user-message",
                    "type": "page",
                    "url": "https://www.zhipin.com/web/geek/chat",
                }
            ]
        ).encode()
        return response

    monkeypatch.setattr(
        "adapters.browser.message_discovery.urlopen",
        fake_open,
    )

    MessageDiscoveryAdapter._close_target(
        "http://127.0.0.1:9222",
        "user-message",
        "https://www.zhipin.com/job_detail/job-1.html",
    )

    assert calls == ["http://127.0.0.1:9222/json/list"]


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://www.zhipin.com/job_detail/boss-1.html", "boss-1"),
        (
            "https://www.liepin.com/job/1983664515.shtml?job_id=liepin-1",
            "liepin-1",
        ),
        ("https://www.liepin.com/a/7823456789.shtml", "7823456789"),
    ],
)
def test_linked_job_id_supports_boss_and_liepin(
    href: str,
    expected: str,
) -> None:
    assert _job_id_from_href(href) == expected


def test_boss_message_page_is_reopened_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = BossMessageDiscoveryAdapter(get_browser_selectors())
    opened: list[object] = []
    response = MagicMock()
    response.__enter__.return_value = response

    def open_page(request: Request, timeout: int) -> MagicMock:
        assert timeout == 3
        opened.append(request)
        return response

    monkeypatch.setattr(adapter, "_matching_list_targets", MagicMock(return_value=[]))
    monkeypatch.setattr(
        "adapters.browser.message_discovery.urlopen",
        open_page,
    )

    assert adapter.ensure_list_page("http://127.0.0.1:9222")
    assert len(opened) == 1
    request = opened[0]
    assert isinstance(request, Request)
    assert request.get_method() == "PUT"
    assert "/json/new?https://www.zhipin.com/web/geek/chat" in request.full_url


def test_location_consent_uses_strategy_onsite_locations() -> None:
    session = MagicMock(spec=Session)
    strategy = SimpleNamespace(
        work_mode_rules=[
            SimpleNamespace(
                work_mode="ONSITE",
                enabled=True,
                locations=[
                    SimpleNamespace(location_name="济南市"),
                ],
            )
        ]
    )
    session.get.return_value = strategy
    run = db.AgentRun(strategy_id=uuid4())

    assert _location_consent_allowed(
        session,
        run,
        "世纪开元文化创意产业园(济南历城区)",
    )
    assert not _location_consent_allowed(
        session,
        run,
        "青岛市市南区",
    )
    strategy.work_mode_rules[0].locations = [
        SimpleNamespace(location_name="青岛市"),
    ]
    assert _location_consent_allowed(
        session,
        run,
        "青岛市市南区",
    )


def test_target_verification_checks_stable_identity_not_recruiter_name_only() -> None:
    selected = summary(1)
    assert _verify_target(selected, detail(selected)) == []

    changed = detail(selected)
    assert changed.conversation is not None
    changed.conversation.job_title = "产品经理"
    assert _verify_target(selected, changed) == ["CONVERSATION_JOB_CHANGED"]


def test_maimai_private_message_discovery_is_disabled() -> None:
    session = MagicMock(spec=Session)
    run = db.AgentRun(id=uuid4(), platform="MAIMAI")
    batch = MessageDiscoveryBatch(
        platform=Platform.MAIMAI,
        partition="UNREAD",
        scroll_position=0,
        scanned_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="不再发现私信消息"):
        persist_discovery_batch(session, run, "worker-1", batch)


def test_maimai_private_conversation_creation_is_disabled() -> None:
    with pytest.raises(ValueError, match="不再建立私信会话"):
        create_conversation(
            MagicMock(spec=Session),
            ConversationPayload(
                platform="MAIMAI",
                external_conversation_id="maimai-chat-1",
                recruiter_name="招聘人",
            ),
        )


def test_derived_boss_identity_uses_recruiter_when_list_role_is_not_job_title() -> None:
    selected = summary(1)
    selected.external_conversation_id = "derived:fixture"
    selected.job_title = "招聘专家"
    result = detail(selected)
    assert result.conversation is not None
    result.conversation.external_conversation_id = "62001"
    result.conversation.job_title = "Java开发工程师"

    assert _verify_target(selected, result) == []


def test_linked_job_is_read_even_when_conversation_has_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MessageDiscoveryAdapter(
        Platform.BOSS,
        get_browser_selectors(),
    )
    selected = summary(1)
    result = detail(selected)
    assert result.conversation is not None
    result.conversation.messages = [
        BrowserMessage(
            external_message_id="message-1",
            content="你好",
            received_at=datetime.now(UTC),
        )
    ]
    page = MagicMock()
    page._evaluate.return_value = True
    linked_job = ReadResult(
        platform=Platform.BOSS,
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB,
        page_url="https://www.zhipin.com/job_detail/job-1.html",
        page_title="Java 后端",
        content_hash="b" * 64,
        selector_version="fixture",
    )
    monkeypatch.setattr(
        "adapters.browser.message_discovery.extract_current_page",
        lambda *_args, **_kwargs: result,
    )
    linked_reader = MagicMock(return_value=linked_job)
    monkeypatch.setattr(adapter, "_read_linked_job", linked_reader)
    monkeypatch.setattr(
        adapter,
        "_linked_job_href",
        lambda _page: "https://www.zhipin.com/job_detail/job-1.html",
    )

    discovered = adapter._open_and_read(
        page,
        selected,
        "http://127.0.0.1:9222",
    )

    assert discovered.job_detail is linked_job
    linked_reader.assert_called_once_with(
        page,
        "http://127.0.0.1:9222",
        cache=None,
    )


def test_bound_unchanged_job_reuses_local_jd_without_opening_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MessageDiscoveryAdapter(
        Platform.BOSS,
        get_browser_selectors(),
    )
    selected = summary(1)
    result = detail(selected)
    assert result.conversation is not None
    result.conversation.messages = [
        BrowserMessage(
            external_message_id="message-1",
            content="有新消息",
            received_at=datetime.now(UTC),
        )
    ]
    page = MagicMock()
    page._evaluate.return_value = True
    monkeypatch.setattr(
        "adapters.browser.message_discovery.extract_current_page",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr(
        adapter,
        "_linked_job_href",
        lambda _page: "https://www.zhipin.com/job_detail/job-1.html",
    )
    linked_reader = MagicMock()
    monkeypatch.setattr(adapter, "_read_linked_job", linked_reader)

    discovered = adapter._open_and_read(
        page,
        selected,
        "http://127.0.0.1:9222",
        known_linked_job_id="job-1",
    )

    assert discovered.job_detail is None
    assert discovered.detail is not None
    assert discovered.detail.conversation is not None
    assert discovered.detail.conversation.external_job_id == "job-1"
    assert discovered.job_source_url == (
        "https://www.zhipin.com/job_detail/job-1.html"
    )
    linked_reader.assert_not_called()


def test_boss_linked_job_url_falls_back_to_verified_component_job_id() -> None:
    adapter = MessageDiscoveryAdapter(
        Platform.BOSS,
        get_browser_selectors(),
    )
    page = MagicMock()
    page.attribute.return_value = None
    page._evaluate.return_value = "daddcedf0f6f79e40nJ609W6FVFR"

    href = adapter._linked_job_href(page)

    assert href == (
        "https://www.zhipin.com/job_detail/"
        "daddcedf0f6f79e40nJ609W6FVFR.html"
    )


def test_linked_job_batch_cache_avoids_reopening_same_job() -> None:
    adapter = MessageDiscoveryAdapter(
        Platform.BOSS,
        get_browser_selectors(),
    )
    page = MagicMock()
    href = "https://www.zhipin.com/job_detail/job-1.html"
    page.url = "https://www.zhipin.com/web/geek/chat"
    page.attribute.return_value = href
    cached = ReadResult(
        platform=Platform.BOSS,
        status=SessionStatus.SESSION_READY,
        page_type=PageType.JOB,
        page_url=href,
        page_title="Java 后端",
        content_hash="b" * 64,
        selector_version="fixture",
    )

    result = adapter._read_linked_job(
        page,
        "http://127.0.0.1:9222",
        cache={href: cached},
    )

    assert result is cached


@pytest.mark.parametrize("value", ["../other", "javascript:alert(1)", "", None])
def test_boss_linked_job_url_rejects_invalid_component_job_id(
    value: object,
) -> None:
    adapter = MessageDiscoveryAdapter(
        Platform.BOSS,
        get_browser_selectors(),
    )
    page = MagicMock()
    page.attribute.return_value = None
    page._evaluate.return_value = value

    assert adapter._linked_job_href(page) is None


def test_cursor_scans_100_reordered_conversations_without_duplicates() -> None:
    items = [summary(index) for index in range(100)]
    seen: list[str] = []
    selected_ids: list[str] = []
    for position in range(0, 100, 10):
        selected = select_discovery_candidates(
            items, seen, scroll_position=position, limit=10
        )
        selected_ids.extend(item.external_conversation_id for item in selected)
        seen.extend(
            f"{item.external_conversation_id}:{item.last_message_id}"
            for item in selected
        )

    reordered = list(reversed(items))
    duplicate_scan = select_discovery_candidates(
        reordered, seen, scroll_position=0, limit=100
    )
    assert len(selected_ids) == 100
    assert len(set(selected_ids)) == 100
    assert duplicate_scan == []

    new_message = summary(50, message=1000)
    changed = [new_message, *[item for item in reordered if item.external_conversation_id != "conversation-50"]]
    assert select_discovery_candidates(
        changed, seen, scroll_position=0, limit=100
    ) == [new_message]


def test_message_key_uses_preview_when_platform_has_no_message_id() -> None:
    item = summary(1)
    item.last_message_id = None
    item.last_message_text = "你好，方便聊聊吗"
    first_key = _message_key(item)

    item.last_message_text = "可以发一份简历吗"

    assert first_key.startswith("conversation-1:preview:")
    assert _message_key(item) != first_key


def test_stable_failed_read_is_seen_until_message_changes() -> None:
    inbound = summary(1)
    outbound_only = summary(2)
    failed = summary(3)
    unstable = summary(4)
    failed_unstable = summary(5)
    unstable.last_message_id = None
    unstable.last_message_text = None
    failed_unstable.last_message_id = None
    failed_unstable.last_message_text = None
    inbound_detail = detail(inbound)
    outbound_detail = detail(outbound_only)
    unstable_detail = detail(unstable)
    assert inbound_detail.conversation is not None
    assert outbound_detail.conversation is not None
    assert unstable_detail.conversation is not None
    inbound_detail.conversation.messages = [
        BrowserMessage(
            external_message_id="inbound-1",
            content="你好",
            received_at=datetime.now(UTC),
            direction="INBOUND",
        )
    ]
    outbound_detail.conversation.messages = [
        BrowserMessage(
            external_message_id="outbound-1",
            content="您好",
            received_at=datetime.now(UTC),
            direction="OUTBOUND",
        )
    ]
    unstable_detail.conversation.messages = [
        BrowserMessage(
            external_message_id="inbound-unstable",
            content="在吗",
            received_at=datetime.now(UTC),
            direction="INBOUND",
        )
    ]

    keys = _attempted_message_keys(
        [inbound, outbound_only, failed, unstable, failed_unstable],
        [
            DiscoveredConversation(summary=inbound, detail=inbound_detail),
            DiscoveredConversation(summary=outbound_only, detail=outbound_detail),
            DiscoveredConversation(
                summary=failed,
                reason_codes=["CONVERSATION_DETAIL_NOT_READY"],
            ),
            DiscoveredConversation(summary=unstable, detail=unstable_detail),
            DiscoveredConversation(
                summary=failed_unstable,
                reason_codes=["CONVERSATION_DETAIL_NOT_READY"],
            ),
        ],
    )

    assert keys == [
        _message_key(inbound),
        _message_key(outbound_only),
        _message_key(failed),
        _message_key(unstable),
        _message_key(failed_unstable),
    ]


def test_unstable_seen_conversation_is_reopened_only_when_unread() -> None:
    item = summary(1)
    item.last_message_id = None
    item.last_message_text = None
    item.unread_count = 0
    seen = [_message_key(item)]

    assert select_discovery_candidates(
        [item], seen, scroll_position=0, limit=10
    ) == []

    item.unread_count = 1

    assert select_discovery_candidates(
        [item], seen, scroll_position=0, limit=10
    ) == [item]


def test_priority_conversation_is_rechecked_even_when_message_was_seen() -> None:
    item = summary(1)

    assert select_discovery_candidates(
        [item],
        [_message_key(item)],
        priority_conversation_ids=[item.external_conversation_id],
        scroll_position=0,
        limit=10,
    ) == [item]


def test_all_active_conversations_are_rechecked_after_full_scan_cooldown() -> None:
    now = datetime.now(UTC)
    batch = MessageDiscoveryBatch(
        platform=Platform.BOSS,
        partition="ALL",
        scroll_position=20,
        scanned_at=now,
        seen_message_keys=[
            "unstable:conversation",
            "stable:message-1",
        ],
        exhausted=True,
    )

    seen, rescanned_at = _next_seen_message_keys(
        batch,
        {"unstable_rescan_at": (now - timedelta(minutes=61)).isoformat()},
        now,
    )

    assert seen == []
    assert rescanned_at == now


def test_unstable_conversations_are_not_reopened_every_scan_cycle() -> None:
    now = datetime.now(UTC)
    batch = MessageDiscoveryBatch(
        platform=Platform.BOSS,
        partition="ALL",
        scroll_position=20,
        scanned_at=now,
        seen_message_keys=["unstable:conversation"],
        exhausted=True,
    )
    last_rescan = now - timedelta(minutes=1)

    seen, rescanned_at = _next_seen_message_keys(
        batch,
        {"unstable_rescan_at": last_rescan.isoformat()},
        now,
    )

    assert seen == ["unstable:conversation"]
    assert rescanned_at == last_rescan


def test_explicit_rejection_terminates_conversation_by_direction() -> None:
    now = datetime.now(UTC)
    outbound = BrowserMessage(
        external_message_id="outbound-decline",
        content="综合考虑后，这次先不继续沟通了，祝招聘顺利。",
        received_at=now,
        direction=MessageDirection.OUTBOUND,
    )
    inbound = BrowserMessage(
        external_message_id="inbound-decline",
        content="您的经历与岗位不太匹配，这次先不推进了。",
        received_at=now,
        direction=MessageDirection.INBOUND,
    )

    outbound_result = _terminal_state_from_messages([outbound])
    inbound_result = _terminal_state_from_messages([inbound])
    assert outbound_result and outbound_result[:2] == (
        "DECLINED",
        "CANDIDATE_EXPLICITLY_DECLINED",
    )
    assert outbound_result[2] is outbound
    assert inbound_result and inbound_result[:2] == (
        "ENDED",
        "RECRUITER_EXPLICITLY_DECLINED",
    )
    assert inbound_result[2] is inbound


def test_rejection_question_does_not_terminate_conversation() -> None:
    message = BrowserMessage(
        external_message_id="question",
        content="您觉得这个工作地点不合适吗？",
        received_at=datetime.now(UTC),
        direction=MessageDirection.INBOUND,
    )

    assert _terminal_state_from_messages([message]) is None


def test_explicit_candidate_correction_supersedes_older_decline() -> None:
    now = datetime.now(UTC)
    decline = BrowserMessage(
        external_message_id="outbound-decline",
        content="综合考虑后，这次先不继续沟通了，祝招聘顺利。",
        received_at=now - timedelta(minutes=1),
        direction=MessageDirection.OUTBOUND,
    )
    correction = BrowserMessage(
        external_message_id="outbound-correction",
        content="抱歉，上面的信息是求职 Agent 发的，这个岗位符合我的方向。",
        received_at=now,
        direction=MessageDirection.OUTBOUND,
    )

    assert _terminal_state_from_messages([decline, correction]) is None


def test_inbound_job_decision_is_saved_without_creating_greeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    run = SimpleNamespace(
        id=uuid4(),
        user_id=DEFAULT_USER_ID,
        strategy_id=uuid4(),
        platform="BOSS",
        cursor={},
    )
    conversation = SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        latest_job_decision_id=None,
    )
    strategy = SimpleNamespace(
        id=run.strategy_id,
        candidate_profile_id=uuid4(),
    )
    decision = SimpleNamespace(id=uuid4(), hard_rejected=False, decision="CONTACT")
    session.scalar.return_value = conversation
    session.get.return_value = strategy
    create_decision = MagicMock(return_value=decision)
    monkeypatch.setattr(
        "apps.api.app.services.message_discovery_service.create_decision",
        create_decision,
    )

    result = process_next_inbound_job_decision(
        session,
        run,  # type: ignore[arg-type]
        FakeLlmProvider(),
    )

    assert result == "DECIDED"
    assert conversation.latest_job_decision_id == decision.id
    create_decision.assert_called_once()
    session.commit.assert_called_once()


def test_all_unread_and_new_greeting_partitions_are_distinct() -> None:
    item = summary(1)
    item.category = "NEW_GREETING"
    assert _matches_partition(item, "ALL")
    assert _matches_partition(item, "UNREAD")
    assert _matches_partition(item, "NEW_GREETING")
    item.unread_count = 0
    item.category = "ALL"
    assert _matches_partition(item, "ALL")
    assert not _matches_partition(item, "UNREAD")
    assert not _matches_partition(item, "NEW_GREETING")


def test_duplicate_platform_ids_use_unique_visible_composite_identity() -> None:
    first = summary(1)
    second = summary(2)
    second.external_conversation_id = first.external_conversation_id

    _normalize_duplicate_conversation_ids([first, second])

    assert first.external_conversation_id.startswith("derived:")
    assert second.external_conversation_id.startswith("derived:")
    assert first.external_conversation_id != second.external_conversation_id
    assert first.identity_reliable is True
    assert second.identity_reliable is True


def test_duplicate_conversation_ids_remain_reliable_with_unique_job_ids() -> None:
    first = summary(1)
    second = summary(2)
    second.external_conversation_id = first.external_conversation_id
    second.external_job_id = "job-2"

    _normalize_duplicate_conversation_ids([first, second])

    assert first.external_conversation_id.startswith("derived:")
    assert second.external_conversation_id.startswith("derived:")
    assert first.external_conversation_id != second.external_conversation_id
    assert first.identity_reliable is True
    assert second.identity_reliable is True


def test_same_recruiter_duplicate_ids_use_unique_visible_identity() -> None:
    first = summary(1)
    first.recruiter_name = "刘女士"
    first.job_title = "刘女士元合共生网络科技人力资源经理"
    first.external_job_id = None
    second = summary(2)
    second.external_conversation_id = first.external_conversation_id
    second.recruiter_name = "刘女士"
    second.job_title = "刘女士中软国际高级HR"
    second.external_job_id = None

    _normalize_duplicate_conversation_ids([first, second])

    assert first.external_conversation_id.startswith("derived:")
    assert second.external_conversation_id.startswith("derived:")
    assert first.external_conversation_id != second.external_conversation_id
    assert first.identity_reliable is True
    assert second.identity_reliable is True


def test_same_recruiter_duplicate_ids_stay_unreliable_without_discriminator() -> None:
    first = summary(1)
    first.recruiter_name = "刘女士"
    first.job_title = None
    first.company_name = None
    first.external_job_id = None
    second = summary(2)
    second.external_conversation_id = first.external_conversation_id
    second.recruiter_name = "刘女士"
    second.job_title = None
    second.company_name = None
    second.external_job_id = None

    _normalize_duplicate_conversation_ids([first, second])

    assert first.identity_reliable is False
    assert second.identity_reliable is False


def test_successful_discovery_records_ready_platform_session() -> None:
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    run = db.AgentRun(platform="BOSS")

    record_ready_platform_session(
        session,
        run=run,
        cdp_url="http://127.0.0.1:9222",
    )

    platform_session = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], db.PlatformSession)
    )
    assert platform_session.platform == "BOSS"
    assert platform_session.status == "SESSION_READY"
    assert platform_session.cdp_endpoint == "http://127.0.0.1:9222"
    session.flush.assert_called_once()
