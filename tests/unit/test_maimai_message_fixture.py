from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from adapters.browser.message_discovery import MaimaiMessageDiscoveryAdapter
from adapters.browser.playwright_reader import PlaywrightPageReader
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.core.recommendation_config import get_recommendation_rules
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import (
    MessageDirection,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "browser" / "maimai"


@contextmanager
def fixture_page(name: str) -> Iterator[Page]:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "https://maimai.cn/**",
            lambda route: route.fulfill(
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=html,
            ),
        )
        page.goto(f"https://maimai.cn/chat/mock/{name}")
        yield page
        browser.close()


def read_fixture(page: Page) -> ReadResult:
    config = get_browser_selectors()
    return extract_current_page(
        PlaywrightPageReader(page),
        Platform.MAIMAI,
        config.platforms[Platform.MAIMAI.value],
        config.version,
    )


def test_reads_maimai_conversation_list_with_stable_ids_and_preview_hash() -> None:
    with fixture_page("conversation-list.html") as page:
        result = read_fixture(page)

    assert result.status is SessionStatus.SESSION_READY
    assert result.page_type is PageType.CONVERSATION_LIST
    assert result.cursor == "maimai-page-2"
    assert result.conversations[0].external_conversation_id == "maimai-chat-1"
    assert result.conversations[0].last_message_id
    assert result.conversations[0].last_message_text == "方便聊一下 Java 后端岗位吗？"
    assert result.conversations[0].unread_count == 2


def test_maimai_ordinary_message_filter_excludes_recommendations_and_official() -> None:
    with fixture_page("conversation-list.html") as page:
        result = read_fixture(page)
    adapter = MaimaiMessageDiscoveryAdapter(
        get_browser_selectors(), get_recommendation_rules()
    )

    included = [
        item.external_conversation_id
        for item in result.conversations
        if adapter._include_summary(item)
    ]

    assert included == ["maimai-chat-1"]


def test_reads_maimai_conversation_detail_and_message_directions() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = read_fixture(page)

    assert result.status is SessionStatus.SESSION_READY
    assert result.page_type is PageType.CONVERSATION
    assert result.conversation is not None
    assert result.conversation.external_conversation_id == "maimai-chat-1"
    assert result.conversation.recruiter_name == "李招聘"
    assert result.conversation.company_name == "示例科技"
    assert result.conversation.job_title == "Java 后端工程师"
    assert result.conversation.external_job_id == "maimai-job-1"
    assert [item.direction for item in result.conversation.messages] == [
        MessageDirection.INBOUND,
        MessageDirection.OUTBOUND,
    ]
