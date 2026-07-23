from adapters.browser.message_discovery import (
    _matches_partition,
    _verify_target,
    select_discovery_candidates,
)
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserConversationSummary,
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
