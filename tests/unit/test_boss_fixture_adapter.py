from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from adapters.browser.playwright_actions import PlaywrightActionExecutor
from adapters.browser.playwright_reader import PlaywrightPageReader
from apps.api.app.core.browser_config import get_browser_selectors
from packages.browser_worker.actions import ApprovedCommand, ExecutionOutcome
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import (
    MessageDirection,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "browser" / "boss"


@contextmanager
def fixture_page(name: str) -> Iterator[Page]:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("https://www.zhipin.com/**", lambda route: route.fulfill(
            status=200, headers={"content-type": "text/html; charset=utf-8"}, body=html
        ))
        page.goto(f"https://www.zhipin.com/mock/{name}")
        yield page
        browser.close()


def read_fixture(page: Page) -> ReadResult:
    config = get_browser_selectors()
    return extract_current_page(
        PlaywrightPageReader(page),
        Platform.BOSS,
        config.platforms["BOSS"],
        config.version,
    )


def approved_command(action_type: str, **changes: str) -> ApprovedCommand:
    values = {
        "action_type": action_type,
        "platform": "BOSS",
        "conversation_key": "boss-chat-1",
        "company": "示例科技",
        "job_title": "高级 Python 后端工程师",
        "recruiter": "张招聘",
    }
    values.update(changes)
    return ApprovedCommand.model_validate(values)


def test_reads_job_list_with_cursor_then_job_detail() -> None:
    with fixture_page("job-list.html") as page:
        result = read_fixture(page)
        assert result.status is SessionStatus.SESSION_READY
        assert result.page_type is PageType.JOB_LIST
        assert result.cursor == "page-2"
        assert [job.external_job_id for job in result.jobs] == ["boss-job-1", "boss-job-2"]

    with fixture_page("job-detail.html") as page:
        result = read_fixture(page)
        assert result.page_type is PageType.JOB
        assert result.job
        assert result.job.external_job_id == "boss-job-1"
        assert result.job.recruiter_name == "张招聘"


def test_reads_conversation_list_and_message_direction() -> None:
    with fixture_page("conversation-list.html") as page:
        result = read_fixture(page)
        assert result.page_type is PageType.CONVERSATION_LIST
        assert result.cursor == "chat-page-2"
        assert result.conversations[0].unread_count == 2

    with fixture_page("conversation-detail.html") as page:
        result = read_fixture(page)
        assert result.page_type is PageType.CONVERSATION
        assert result.conversation
        assert result.conversation.messages[0].direction is MessageDirection.INBOUND


def test_sends_text_and_reads_back_result_on_fixture_page() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page, approved_command("REPLY", content="您好，可以发送。")
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert page.locator("[data-direction='outbound']").filter(
            has_text="您好，可以发送。"
        ).count() == 1


def test_selects_unique_existing_resume_and_reads_back_result() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page, approved_command("RESUME", attachment_name="后端开发简历")
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert page.locator("[data-testid='sent-resume']").filter(
            has_text="后端开发简历"
        ).count() == 1


def test_changed_page_stops_before_any_write() -> None:
    with fixture_page("changed-page.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page, approved_command("REPLY", content="不应发送")
        )
        assert result.outcome is ExecutionOutcome.FAILED_FINAL
        assert result.error_code == "SUPPORTED_PAGE_ROOT_NOT_FOUND"
        assert page.get_by_text("不应发送").count() == 0
