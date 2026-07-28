from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from adapters.browser.message_discovery import (
    MessageDiscoveryAdapter,
    _matches_partition,
    _normalize_duplicate_conversation_ids,
    _verify_target,
    select_discovery_candidates,
)
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.models import entities as db
from apps.api.app.services.message_discovery_service import (
    record_ready_platform_session,
)
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserConversationSummary,
    BrowserMessage,
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


def test_target_verification_checks_stable_identity_not_recruiter_name_only() -> None:
    selected = summary(1)
    assert _verify_target(selected, detail(selected)) == []

    changed = detail(selected)
    assert changed.conversation is not None
    changed.conversation.job_title = "产品经理"
    assert _verify_target(selected, changed) == ["CONVERSATION_JOB_CHANGED"]


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

    discovered = adapter._open_and_read(
        page,
        selected,
        "http://127.0.0.1:9222",
    )

    assert discovered.job_detail is linked_job
    linked_reader.assert_called_once_with(page, "http://127.0.0.1:9222")


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


def test_duplicate_platform_conversation_ids_get_stable_composite_ids() -> None:
    first = summary(1)
    second = summary(2)
    second.external_conversation_id = first.external_conversation_id

    _normalize_duplicate_conversation_ids([first, second])

    assert first.external_conversation_id.startswith("derived:")
    assert second.external_conversation_id.startswith("derived:")
    assert first.external_conversation_id != second.external_conversation_id


def test_successful_discovery_records_ready_platform_session() -> None:
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    run = db.AgentRun(platform="BOSS")

    record_ready_platform_session(
        session,
        run=run,
        cdp_url="http://127.0.0.1:9222",
    )

    platform_session = session.add.call_args.args[0]
    assert isinstance(platform_session, db.PlatformSession)
    assert platform_session.platform == "BOSS"
    assert platform_session.status == "SESSION_READY"
    assert platform_session.cdp_endpoint == "http://127.0.0.1:9222"
    session.flush.assert_called_once()
