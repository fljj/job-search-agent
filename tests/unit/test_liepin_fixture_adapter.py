from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

from adapters.browser.playwright_actions import PlaywrightActionExecutor
from adapters.browser.playwright_reader import PlaywrightPageReader, _current_cdp_target
from apps.api.app.core.browser_config import get_browser_selectors
from packages.browser_worker.actions import ApprovedCommand, ExecutionOutcome
from packages.browser_worker.extractor import _job_id_from_url, extract_current_page
from packages.browser_worker.models import (
    MessageDirection,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "browser" / "liepin"


@contextmanager
def fixture_page(name: str) -> Iterator[Page]:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "https://c.liepin.com/**",
            lambda route: route.fulfill(
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=html,
            ),
        )
        page.goto(f"https://c.liepin.com/mock/{name}")
        yield page
        browser.close()


def read_fixture(page: Page, **expected: str) -> ReadResult:
    config = get_browser_selectors()
    selectors = config.platforms[Platform.LIEPIN.value]
    return extract_current_page(
        PlaywrightPageReader(page),
        Platform.LIEPIN,
        selectors,
        selectors.version,
        **expected,
    )


def test_reads_home_job_list_and_normalizes_both_link_forms() -> None:
    with fixture_page("job-list.html") as page:
        result = read_fixture(page)

    assert result.status is SessionStatus.SESSION_READY
    assert result.page_type is PageType.JOB_LIST
    assert result.cursor == "liepin-page-2"
    assert [job.external_job_id for job in result.jobs] == [
        "job-liepin-1",
        "job-liepin-2",
    ]
    assert _job_id_from_url("https://www.liepin.com/job/1983664515.shtml") == "1983664515"
    assert _job_id_from_url("https://www.liepin.com/a/7823456789.shtml") == "7823456789"


def test_reads_job_detail_with_platform_selector_version() -> None:
    with fixture_page("job-detail.html") as page:
        result = read_fixture(
            page,
            expected_company="示例科技",
            expected_job_title="Java 后端工程师",
        )

    assert result.status is SessionStatus.SESSION_READY
    assert result.page_type is PageType.JOB
    assert result.selector_version == "2026-08-01-v7"
    assert result.job is not None
    assert result.job.external_job_id == "job-liepin-1"
    assert result.job.company_name == "示例科技"
    assert result.job.recruiter_name == "李女士"
    assert result.job.recruiter_role == "DIRECT_EMPLOYER"
    assert result.job.work_mode == "ONSITE"


def test_reads_encoded_conversation_ids_and_message_directions() -> None:
    with fixture_page("conversation-list.html") as page:
        listing = read_fixture(page)
    with fixture_page("conversation-detail.html") as page:
        detail = read_fixture(page, expected_recruiter="李女士")

    assert listing.page_type is PageType.CONVERSATION_LIST
    assert listing.conversations[0].external_conversation_id == "liepin-chat-1"
    assert listing.conversations[0].unread_count == 1
    assert listing.conversations[0].company_name == "示例科技"
    assert listing.conversations[0].job_title == "Java 后端工程师"
    assert detail.page_type is PageType.CONVERSATION
    assert detail.conversation is not None
    assert detail.conversation.external_conversation_id == "liepin-chat-1"
    assert detail.conversation.company_name == "示例科技"
    assert [message.direction for message in detail.conversation.messages] == [
        MessageDirection.INBOUND,
        MessageDirection.OUTBOUND,
    ]


def test_abnormal_pages_fail_closed_with_explicit_status() -> None:
    with fixture_page("login-required.html") as page:
        login = read_fixture(page)
    with fixture_page("verification-required.html") as page:
        verification = read_fixture(page)
    with fixture_page("changed-page.html") as page:
        changed = read_fixture(page)
    with fixture_page("job-detail.html") as page:
        mismatch = read_fixture(page, expected_company="其他公司")

    assert login.status is SessionStatus.SESSION_AUTH_REQUIRED
    assert login.reason_codes == ["LOGIN_REQUIRED"]
    assert verification.status is SessionStatus.SESSION_AUTH_REQUIRED
    assert verification.reason_codes == ["VERIFICATION_REQUIRED"]
    assert changed.status is SessionStatus.SESSION_PAGE_CHANGED
    assert changed.reason_codes == ["SUPPORTED_PAGE_ROOT_NOT_FOUND"]
    assert mismatch.status is SessionStatus.SESSION_TARGET_MISMATCH
    assert mismatch.reason_codes == ["JOB_TARGET_MISMATCH"]


def test_write_confirmation_fixtures_have_unique_read_only_targets() -> None:
    selectors = get_browser_selectors().platforms[Platform.LIEPIN.value]
    with fixture_page("greeting-confirm.html") as page:
        assert page.locator(selectors.platform_greeting_dialog).count() == 1
        assert page.locator(selectors.platform_greeting_message).count() == 1
    with fixture_page("resume-confirm.html") as page:
        assert page.locator(selectors.resume_items).count() == 1
        assert page.locator(selectors.resume_confirm_button).count() == 1


def _approved_command(action_type: str, **changes: str | None) -> ApprovedCommand:
    values: dict[str, str | None] = {
        "action_type": action_type,
        "platform": "LIEPIN",
        "conversation_key": "liepin-chat-1",
        "external_job_id": "job-liepin-1",
        "company": "示例科技",
        "job_title": "Java 后端工程师",
        "recruiter": "李女士",
    }
    values.update(changes)
    return ApprovedCommand.model_validate(values)


def test_liepin_greeting_click_is_read_back_once() -> None:
    with fixture_page("action-job.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            _approved_command(
                "GREETING",
                conversation_key=None,
                content="内部草稿不作为第二条消息发送",
                delivery_mode="PLATFORM_DEFAULT",
            ),
        )

        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert result.observed_content == "您好，希望进一步沟通。"
        assert page.locator(".im-ui-message-item-send").count() == 1


def test_liepin_reply_uses_approved_conversation_and_reads_back() -> None:
    with fixture_page("action-conversation.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            _approved_command("REPLY", content="您好，可以进一步沟通。"),
        )

        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert page.locator(".im-ui-message-item-send").filter(
            has_text="您好，可以进一步沟通。"
        ).count() == 1


def test_liepin_conversation_list_target_decodes_stable_im_id() -> None:
    class EvaluationPage:
        def __init__(self, page: Page) -> None:
            self.page = page
            self.commands: list[str] = []

        def _evaluate(self, expression: str) -> object:
            return self.page.evaluate(expression)

        def _command(self, method: str, _params: dict[str, object]) -> dict[str, object]:
            self.commands.append(method)
            return {}

    with fixture_page("action-conversation.html") as page:
        raw_page = EvaluationPage(page)
        opened = PlaywrightActionExecutor._open_approved_conversation(
            raw_page,  # type: ignore[arg-type]
            get_browser_selectors().platforms["LIEPIN"],
            _approved_command("REPLY", content="您好"),
        )

    assert opened
    assert raw_page.commands == ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent"]


def test_liepin_resume_selects_only_registered_existing_resume() -> None:
    with fixture_page("action-conversation.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            _approved_command(
                "RESUME",
                attachment_name="已维护的默认简历",
            ),
        )

        assert result.outcome is ExecutionOutcome.SUCCEEDED
        assert page.locator(".im-ui-message-item-send").filter(
            has_text="已维护的默认简历"
        ).count() == 1


def test_liepin_resume_name_mismatch_stops_before_submission() -> None:
    with fixture_page("action-conversation.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            _approved_command(
                "RESUME",
                attachment_name="未登记的简历",
            ),
        )

        assert result.outcome is ExecutionOutcome.FAILED_FINAL
        assert result.error_code == "ATTACHMENT_TARGET_MISMATCH"
        assert page.locator(".im-ui-message-item-send").filter(
            has_text="未登记的简历"
        ).count() == 0


def test_liepin_changed_target_stops_before_write() -> None:
    with fixture_page("action-conversation.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            _approved_command(
                "REPLY",
                recruiter="其他招聘人",
                content="不应发送",
            ),
        )

        assert result.outcome is ExecutionOutcome.FAILED_FINAL
        assert page.get_by_text("不应发送").count() == 0


def test_liepin_unobserved_write_is_not_reported_as_success() -> None:
    with fixture_page("conversation-detail.html") as page:
        result = PlaywrightActionExecutor(get_browser_selectors()).execute_on_page(
            page,
            _approved_command("REPLY", content="无法回读的回复"),
        )

        assert result.outcome is ExecutionOutcome.OUTCOME_UNKNOWN
        assert result.error_code == "RESULT_NOT_OBSERVED"


def test_shared_home_conflict_fixture_preserves_user_input_and_dialog() -> None:
    with fixture_page("shared-home-conflict.html") as page:
        result = read_fixture(page)
        assert page.locator("textarea.im-ui-textarea").input_value() == "用户尚未发送的内容"
        assert page.locator(".ant-modal-wrap").is_visible()
        page.locator("textarea.im-ui-textarea").fill("")
        dialog_result = read_fixture(page)
    assert result.status is SessionStatus.SESSION_PAUSED
    assert result.reason_codes == ["PENDING_USER_INPUT"]
    assert dialog_result.status is SessionStatus.SESSION_PAUSED
    assert dialog_result.reason_codes == ["BLOCKING_DIALOG_VISIBLE"]


def test_duplicate_liepin_home_targets_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = (FIXTURES / "duplicate-home-targets.json").read_bytes()

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    monkeypatch.setattr(
        "adapters.browser.playwright_reader.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(ValueError, match="多个猎聘首页"):
        _current_cdp_target(
            "http://127.0.0.1:9222",
            ["c.liepin.com", "www.liepin.com"],
            unique_home_host="c.liepin.com",
        )
