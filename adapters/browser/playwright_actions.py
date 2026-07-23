import hashlib

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from adapters.browser.playwright_reader import (
    PlaywrightPageReader,
    _current_page,
    validate_local_cdp_url,
)
from packages.browser_worker.actions import ApprovedCommand, ExecutionOutcome, ExecutionResult
from packages.browser_worker.config import BrowserSelectorsConfig
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import Platform, SessionStatus


class PlaywrightActionExecutor:
    """仅执行已由服务端原子批准的单个动作。"""

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        self.config = config

    def execute(self, cdp_url: str, command: ApprovedCommand) -> ExecutionResult:
        validate_local_cdp_url(cdp_url)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                page = _current_page(browser)
                return self.execute_on_page(page, command)
        except PlaywrightError:
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="PLAYWRIGHT_ERROR",
            )

    def execute_on_page(self, page: Page, command: ApprovedCommand) -> ExecutionResult:
        """在已打开页面执行单个动作，供受控 CDP 与本地页面夹具共同使用。"""
        performed = False
        try:
            platform = Platform(command.platform)
            selectors = self.config.platforms[platform.value]
            reader = PlaywrightPageReader(page)
            check = extract_current_page(
                reader,
                platform,
                selectors,
                self.config.version,
                expected_company=command.company,
                expected_job_title=command.job_title,
                expected_recruiter=command.recruiter,
            )
            if check.status is not SessionStatus.SESSION_READY:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_FINAL,
                    error_code=check.reason_codes[0] if check.reason_codes else "PREFLIGHT_FAILED",
                )
            if command.action_type == "GREETING":
                if not check.job:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="JOB_PAGE_REQUIRED",
                    )
                if (
                    command.external_job_id
                    and check.job.external_job_id != command.external_job_id
                ):
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="JOB_TARGET_MISMATCH",
                    )
                if (
                    not check.job.recruiter_name
                    or command.recruiter not in check.job.recruiter_name
                    or check.job.source_status != "OPEN"
                ):
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="RECRUITER_OR_JOB_STATUS_MISMATCH",
                    )
                if not command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="EMPTY_CONTENT",
                    )
                page.locator(selectors.job_open_marker).first.click()
                performed = True
                page.locator(selectors.message_composer).wait_for(
                    state="visible", timeout=3000
                )
                page.locator(selectors.message_composer).fill(command.content)
                page.locator(selectors.message_send_button).click()
                page.wait_for_timeout(50)
                matched = page.locator(selectors.sent_message_items).filter(
                    has_text=command.content
                )
            elif not check.conversation:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_FINAL,
                    error_code="CONVERSATION_PAGE_REQUIRED",
                )
            elif check.conversation.external_conversation_id != command.conversation_key:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_FINAL,
                    error_code="CONVERSATION_TARGET_MISMATCH",
                )
            elif (
                not check.conversation.company_name
                or command.company not in check.conversation.company_name
                or not check.conversation.job_title
                or command.job_title not in check.conversation.job_title
            ):
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_FINAL,
                    error_code="JOB_TARGET_MISMATCH",
                )
            elif command.action_type == "REPLY":
                if not command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL, error_code="EMPTY_CONTENT"
                    )
                page.locator(selectors.message_composer).fill(command.content)
                page.locator(selectors.message_send_button).click()
                performed = True
                page.wait_for_timeout(50)
                matched = page.locator(selectors.sent_message_items).filter(
                    has_text=command.content
                )
            elif command.action_type == "RESUME":
                if not command.attachment_name:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL, error_code="ATTACHMENT_MISSING"
                    )
                page.locator(selectors.resume_trigger).click()
                item = page.locator(selectors.resume_items).filter(
                    has_text=command.attachment_name
                )
                if item.count() != 1:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="ATTACHMENT_TARGET_MISMATCH",
                    )
                item.click()
                page.locator(selectors.resume_confirm_button).click()
                performed = True
                page.wait_for_timeout(50)
                matched = page.locator(selectors.sent_resume_items).filter(
                    has_text=command.attachment_name
                )
            else:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_FINAL, error_code="UNSUPPORTED_ACTION"
                )
            evidence = hashlib.sha256(
                f"{page.url}:{command.model_dump_json()}".encode()
            ).hexdigest()
            if matched.count():
                return ExecutionResult(
                    outcome=ExecutionOutcome.SUCCEEDED, evidence_hash=evidence
                )
            return ExecutionResult(
                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                error_code="RESULT_NOT_OBSERVED",
                evidence_hash=evidence,
            )
        except PlaywrightError:
            return ExecutionResult(
                outcome=(
                    ExecutionOutcome.OUTCOME_UNKNOWN
                    if performed
                    else ExecutionOutcome.FAILED_RETRYABLE
                ),
                error_code="PLAYWRIGHT_ERROR",
            )
