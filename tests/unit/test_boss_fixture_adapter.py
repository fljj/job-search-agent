from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

from playwright.sync_api import Page, sync_playwright

from adapters.browser.playwright_actions import (
    PlaywrightActionExecutor,
    _reply_target_matches,
)
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


def approved_command(action_type: str, **changes: str | None) -> ApprovedCommand:
    values: dict[str, str | None] = {
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
        assert result.job.company_name == "示例科技"
        assert result.job.recruiter_name == "张招聘"


def test_reads_conversation_list_and_message_direction() -> None:
    with fixture_page("conversation-list.html") as page:
        result = read_fixture(page)
        assert result.page_type is PageType.CONVERSATION_LIST
        assert result.cursor == "chat-page-2"
        assert result.conversations[0].unread_count == 2
        assert result.conversations[0].external_job_id == "boss-job-1"
        assert result.conversations[0].last_message_id == "message-1"
        assert result.conversations[0].category == "NEW_GREETING"

    with fixture_page("conversation-detail.html") as page:
        result = read_fixture(page)
        assert result.page_type is PageType.CONVERSATION
        assert result.conversation
        assert result.conversation.external_job_id == "boss-job-1"
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


def test_reply_executor_opens_unique_derived_conversation_by_exact_identity() -> None:
    executor = PlaywrightActionExecutor(get_browser_selectors())
    page = MagicMock()
    page._evaluate.return_value = {"x": 100, "y": 200}
    command = approved_command(
        "REPLY",
        conversation_key="derived:approved-conversation",
        content="您好，可以发送。",
    )

    opened = executor._open_approved_conversation(
        page,
        get_browser_selectors().platforms["BOSS"],
        command,
    )

    assert opened
    page._evaluate.assert_called_once()
    assert page._command.call_count == 2


def test_reply_executor_rejects_ambiguous_derived_conversation_identity() -> None:
    executor = PlaywrightActionExecutor(get_browser_selectors())
    page = MagicMock()
    page._evaluate.return_value = None
    command = approved_command(
        "REPLY",
        conversation_key="derived:approved-conversation",
        content="您好，可以发送。",
    )

    opened = executor._open_approved_conversation(
        page,
        get_browser_selectors().platforms["BOSS"],
        command,
    )

    assert not opened
    page._command.assert_not_called()


def test_reply_executor_scrolls_virtual_list_without_refreshing(
    monkeypatch,
) -> None:
    executor = PlaywrightActionExecutor(get_browser_selectors())
    page = MagicMock()
    page._evaluate.side_effect = [
        True,
        {"before": 0, "after": 200, "done": False},
    ]
    open_target = MagicMock(side_effect=[False, False, True])
    monkeypatch.setattr(
        PlaywrightActionExecutor,
        "_open_approved_conversation",
        open_target,
    )
    monkeypatch.setattr("adapters.browser.playwright_actions.time.sleep", lambda _: None)

    opened = executor._find_and_open_approved_conversation(
        page,
        get_browser_selectors().platforms["BOSS"],
        approved_command("RESUME", attachment_name="后端开发简历"),
    )

    assert opened
    assert open_target.call_count == 3
    expressions = [call.args[0] for call in page._evaluate.call_args_list]
    assert all("location.reload" not in expression for expression in expressions)


def test_reply_target_is_reverified_after_opening_conversation() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = read_fixture(page)
        assert _reply_target_matches(result, approved_command("REPLY"))
        assert not _reply_target_matches(
            result,
            approved_command("REPLY", recruiter="其他招聘人"),
        )


def test_derived_reply_target_accepts_boss_headhunter_display_fields() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = read_fixture(page)
        assert result.conversation
        result.conversation.external_conversation_id = "62001"
        result.conversation.job_title = "高级 Python 后端工程师（猎头职位）"
        result.conversation.company_name = None

        assert _reply_target_matches(
            result,
            approved_command(
                "RESUME",
                conversation_key="derived:approved-conversation",
                attachment_name="后端开发简历",
            ),
        )


def test_starts_job_conversation_and_sends_greeting_once() -> None:
    with fixture_page("job-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            approved_command(
                "GREETING",
                conversation_key=None,
                external_job_id="boss-job-1",
                content="您好，我有多年 Python 后端经验，希望进一步沟通。",
            ),
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert page.locator("[data-direction='outbound']").count() == 1


def test_observes_matching_platform_default_greeting_without_second_message() -> None:
    expected = (
        "您好，我对这个岗位很感兴趣，我的经历与岗位要求有一定匹配，"
        "希望能进一步沟通了解，谢谢。"
    )
    with fixture_page("job-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            approved_command(
                "GREETING",
                conversation_key=None,
                external_job_id="boss-job-1",
                content="不会作为第二条消息发送",
                delivery_mode="PLATFORM_DEFAULT",
                expected_platform_content=expected,
            ),
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert result.observed_content == expected
        assert page.locator("[data-direction='outbound']").count() == 0


def test_accepts_any_non_empty_platform_default_when_content_is_not_pinned() -> None:
    with fixture_page("job-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            approved_command(
                "GREETING",
                conversation_key=None,
                external_job_id="boss-job-1",
                content="仅用于内部生成记录",
                delivery_mode="PLATFORM_DEFAULT",
                expected_platform_content=None,
            ),
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert result.observed_content
        assert page.locator("[data-direction='outbound']").count() == 0


def test_greeting_target_mismatch_stops_before_click() -> None:
    with fixture_page("job-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            approved_command(
                "GREETING",
                conversation_key=None,
                external_job_id="another-job",
                content="不应发送",
            ),
        )
        assert result.outcome is ExecutionOutcome.FAILED_FINAL
        assert result.error_code == "JOB_TARGET_MISMATCH"
        assert page.locator("[data-testid='chat-panel']").is_hidden()


def test_greeting_selects_unique_approved_job_among_multiple_tabs() -> None:
    with fixture_page("job-detail.html") as job_page:
        browser = job_page.context.browser
        assert browser is not None
        other = browser.new_page()
        other.route(
            "https://www.zhipin.com/**",
            lambda route: route.fulfill(
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body="<div data-testid='user-avatar'>已登录</div><main>首页</main>",
            ),
        )
        other.goto("https://www.zhipin.com/")
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_browser(
            browser,
            approved_command(
                "GREETING",
                conversation_key=None,
                external_job_id="boss-job-1",
                content="您好，希望进一步沟通。",
            ),
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert job_page.locator("[data-direction='outbound']").count() == 1


def test_missing_approved_job_tab_is_retryable_before_any_click() -> None:
    with fixture_page("changed-page.html") as page:
        browser = page.context.browser
        assert browser is not None
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_browser(
            browser,
            approved_command(
                "GREETING",
                conversation_key=None,
                external_job_id="boss-job-1",
                content="不应发送",
            ),
        )
        assert result.outcome is ExecutionOutcome.FAILED_RETRYABLE
        assert result.error_code == "APPROVED_TARGET_PAGE_NOT_FOUND"
        assert page.get_by_text("不应发送").count() == 0


def test_selects_unique_existing_resume_and_reads_back_result() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page, approved_command("RESUME", attachment_name="后端开发简历")
        )
        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert page.locator("[data-testid='sent-resume']").filter(
            has_text="后端开发简历"
        ).count() == 1


def test_real_boss_resume_trigger_is_restricted_by_visible_text() -> None:
    page = MagicMock()
    page._evaluate.return_value = {"x": 10, "y": 20}
    selectors = get_browser_selectors().platforms["BOSS"]

    point = PlaywrightActionExecutor._visible_element_point_by_text(
        page,
        selectors.resume_trigger,
        {"发简历", "发送简历"},
    )

    assert point == {"x": 10, "y": 20}
    script = page._evaluate.call_args.args[0]
    assert ".toolbar-btn-content .toolbar-btn" in script
    assert "发简历" in script


def test_real_boss_default_resume_uses_direct_confirmation(monkeypatch) -> None:
    page = MagicMock()
    page._evaluate.side_effect = [0, 0, None, {"x": 10, "y": 20}, {"x": 30, "y": 40}, True]
    reader = MagicMock()
    reader.__enter__.return_value = page
    monkeypatch.setattr(
        "adapters.browser.playwright_actions.RawCdpPageReader",
        lambda _: reader,
    )

    result = PlaywrightActionExecutor(get_browser_selectors())._send_resume_on_raw_page(
        "ws://127.0.0.1/devtools/page/test",
        approved_command("RESUME", attachment_name="后端开发简历"),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.write_started
    assert page._command.call_count == 4


def test_real_boss_does_not_fall_back_to_legacy_attachment_list(monkeypatch) -> None:
    page = MagicMock()
    page._evaluate.side_effect = [0, 0, None, {"x": 10, "y": 20}, None]
    reader = MagicMock()
    reader.__enter__.return_value = page
    monkeypatch.setattr(
        "adapters.browser.playwright_actions.RawCdpPageReader",
        lambda _: reader,
    )

    result = PlaywrightActionExecutor(get_browser_selectors())._send_resume_on_raw_page(
        "ws://127.0.0.1/devtools/page/test",
        approved_command("RESUME", attachment_name="后端开发简历"),
    )

    assert result.outcome is ExecutionOutcome.FAILED_RETRYABLE
    assert result.error_code == "RESUME_CONFIRM_NOT_READY"
    assert not result.write_started
    assert page._command.call_count == 2


def test_inbound_resume_rejects_changed_company_identity() -> None:
    with fixture_page("conversation-detail.html") as page:
        browser = page.context.browser
        assert browser is not None
        command = approved_command(
            "RESUME",
            conversation_key="boss-chat-1",
            attachment_name="后端开发简历",
        ).model_copy(update={"company": "代招客户公司"})

        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_browser(
            browser, command
        )

        assert result.outcome is ExecutionOutcome.FAILED_RETRYABLE
        assert result.error_code == "APPROVED_TARGET_PAGE_NOT_FOUND"
        assert page.locator("[data-testid='sent-resume']").count() == 0


def test_changed_page_stops_before_any_write() -> None:
    with fixture_page("changed-page.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page, approved_command("REPLY", content="不应发送")
        )
        assert result.outcome is ExecutionOutcome.FAILED_FINAL
        assert result.error_code == "SUPPORTED_PAGE_ROOT_NOT_FOUND"
        assert page.get_by_text("不应发送").count() == 0
