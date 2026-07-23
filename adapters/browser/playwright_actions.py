import hashlib
import json
import time
from urllib.request import urlopen

from playwright.sync_api import (
    Browser,
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)

from adapters.browser.playwright_reader import (
    PlaywrightPageReader,
    RawCdpPageReader,
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
        if command.action_type == "GREETING":
            return self._execute_greeting_over_raw_cdp(cdp_url, command)
        if command.action_type in {"REPLY", "LOW_SCORE_DECLINE"}:
            return self._execute_reply_over_raw_cdp(cdp_url, command)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                return self.execute_on_browser(browser, command)
        except PlaywrightError:
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="PLAYWRIGHT_ERROR",
            )

    def _execute_greeting_over_raw_cdp(
        self,
        cdp_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        """真实职位页使用原生 CDP，避免 Playwright 附加触发平台页面重定向。"""
        platform = Platform(command.platform)
        selectors = self.config.platforms[platform.value]
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            matches: list[str] = []
            for target in targets:
                websocket_url = target.get("webSocketDebuggerUrl")
                if target.get("type") != "page" or not websocket_url:
                    continue
                with RawCdpPageReader(websocket_url) as page:
                    check = extract_current_page(
                        page,
                        platform,
                        selectors,
                        self.config.version,
                    )
                if (
                    check.status is SessionStatus.SESSION_READY
                    and check.job
                    and command.company in check.job.company_name
                    and command.job_title in check.job.title
                    and command.recruiter in (check.job.recruiter_name or "")
                    and (
                        not command.external_job_id
                        or check.job.external_job_id == command.external_job_id
                    )
                ):
                    matches.append(str(websocket_url))
            if not matches:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="APPROVED_TARGET_PAGE_NOT_FOUND",
                )
            if len(matches) > 1:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="APPROVED_TARGET_PAGE_AMBIGUOUS",
                )
            return self._send_greeting_on_raw_page(matches[0], command)
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="RAW_CDP_PREFLIGHT_ERROR",
            )

    def _execute_reply_over_raw_cdp(
        self,
        cdp_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        """在唯一匹配的真实会话页发送已批准回复，避免 Playwright 接管标签页。"""
        platform = Platform(command.platform)
        selectors = self.config.platforms[platform.value]
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            matches: list[str] = []
            for target in targets:
                websocket_url = target.get("webSocketDebuggerUrl")
                if target.get("type") != "page" or not websocket_url:
                    continue
                with RawCdpPageReader(websocket_url) as page:
                    check = extract_current_page(
                        page, platform, selectors, self.config.version
                    )
                if (
                    check.status is SessionStatus.SESSION_READY
                    and check.conversation
                    and check.conversation.external_conversation_id
                    == command.conversation_key
                    and command.recruiter in check.conversation.recruiter_name
                    and command.job_title in (check.conversation.job_title or "")
                ):
                    matches.append(str(websocket_url))
            if not matches:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="APPROVED_TARGET_PAGE_NOT_FOUND",
                )
            if len(matches) > 1:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="APPROVED_TARGET_PAGE_AMBIGUOUS",
                )
            return self._send_reply_on_raw_page(matches[0], command)
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="RAW_CDP_PREFLIGHT_ERROR",
            )

    def _send_reply_on_raw_page(
        self,
        websocket_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        selectors = self.config.platforms[command.platform]
        performed = False
        try:
            with RawCdpPageReader(websocket_url) as page:
                if not command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="EMPTY_CONTENT",
                    )
                already_sent = page._evaluate(
                    "Array.from(document.querySelectorAll("
                    f"{json.dumps(selectors.sent_message_items)}"
                    f")).some(item => (item.textContent || '').includes("
                    f"{json.dumps(command.content)}))"
                )
                if already_sent:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.SUCCEEDED,
                        evidence_hash=hashlib.sha256(
                            f"{page.url}:{command.model_dump_json()}".encode()
                        ).hexdigest(),
                        observed_content=command.content,
                    )
                filled = page._evaluate(
                    "(() => { const element = document.querySelector("
                    f"{json.dumps(selectors.message_composer)}"
                    f"); if (!element) return false; const value = {json.dumps(command.content)}; "
                    "element.focus(); element.textContent = value; "
                    "element.dispatchEvent(new InputEvent('input', {bubbles: true, "
                    "inputType: 'insertText', data: value})); return "
                    "(element.textContent || '').trim() === value; })()"
                )
                if not filled:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="COMPOSER_FILL_NOT_CONFIRMED",
                    )
                performed = True
                for _ in range(30):
                    sent = page._evaluate(
                        "(() => { const composer = document.querySelector("
                        f"{json.dumps(selectors.message_composer)}"
                        f"); if ((composer?.textContent || '').trim() !== "
                        f"{json.dumps(command.content)}) return false; "
                        "const element = Array.from(document.querySelectorAll("
                        f"{json.dumps(selectors.message_send_button)}"
                        ")).find(item => item.getClientRects().length > 0 "
                        "&& !item.classList.contains('disabled')); "
                        "if (!element) return false; element.click(); return true; })()"
                    )
                    if sent:
                        break
                    time.sleep(0.1)
                else:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="SEND_BUTTON_NOT_READY",
                    )
                for _ in range(30):
                    observed = page._evaluate(
                        "Array.from(document.querySelectorAll("
                        f"{json.dumps(selectors.sent_message_items)}"
                        f")).some(item => (item.textContent || '').includes("
                        f"{json.dumps(command.content)}))"
                    )
                    if observed:
                        return ExecutionResult(
                            outcome=ExecutionOutcome.SUCCEEDED,
                            evidence_hash=hashlib.sha256(
                                f"{page.url}:{command.model_dump_json()}".encode()
                            ).hexdigest(),
                            observed_content=command.content,
                        )
                    time.sleep(0.1)
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="RESULT_NOT_OBSERVED",
                )
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=(
                    ExecutionOutcome.OUTCOME_UNKNOWN
                    if performed
                    else ExecutionOutcome.FAILED_RETRYABLE
                ),
                error_code="RAW_CDP_ACTION_ERROR",
            )

    def observe(self, cdp_url: str, command: ApprovedCommand) -> ExecutionResult:
        """只读对账文本动作；确认唯一目标页后判断精确内容是否已经出现。"""
        validate_local_cdp_url(cdp_url)
        if command.action_type not in {"REPLY", "LOW_SCORE_DECLINE"}:
            return ExecutionResult(
                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                error_code="RECONCILIATION_NOT_SUPPORTED",
            )
        platform = Platform(command.platform)
        selectors = self.config.platforms[platform.value]
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            matches: list[str] = []
            for target in targets:
                websocket_url = target.get("webSocketDebuggerUrl")
                if target.get("type") != "page" or not websocket_url:
                    continue
                with RawCdpPageReader(websocket_url) as page:
                    check = extract_current_page(
                        page, platform, selectors, self.config.version
                    )
                if (
                    check.status is SessionStatus.SESSION_READY
                    and check.conversation
                    and check.conversation.external_conversation_id
                    == command.conversation_key
                    and command.recruiter in check.conversation.recruiter_name
                    and command.job_title in (check.conversation.job_title or "")
                ):
                    matches.append(str(websocket_url))
            if len(matches) != 1:
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code=(
                        "APPROVED_TARGET_PAGE_NOT_FOUND"
                        if not matches
                        else "APPROVED_TARGET_PAGE_AMBIGUOUS"
                    ),
                )
            with RawCdpPageReader(matches[0]) as page:
                observed = bool(page._evaluate(
                    "Array.from(document.querySelectorAll("
                    f"{json.dumps(selectors.sent_message_items)}"
                    f")).some(item => (item.textContent || '').includes("
                    f"{json.dumps(command.content)}))"
                ))
                if observed:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.SUCCEEDED,
                        evidence_hash=hashlib.sha256(
                            f"{page.url}:{command.model_dump_json()}".encode()
                        ).hexdigest(),
                        observed_content=command.content,
                    )
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="RESULT_CONFIRMED_NOT_SENT",
                )
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                error_code="RECONCILIATION_READ_ERROR",
            )

    def _send_greeting_on_raw_page(
        self,
        websocket_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        selectors = self.config.platforms[command.platform]
        performed = False
        try:
            with RawCdpPageReader(websocket_url) as page:
                if not command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_FINAL,
                        error_code="EMPTY_CONTENT",
                    )
                clicked = page._evaluate(
                    "(() => { const element = Array.from(document.querySelectorAll("
                    f"{json.dumps(selectors.job_open_marker)}"
                    ")).find(item => item.getClientRects().length > 0); "
                    "if (!element) return false; element.click(); return true; })()"
                )
                if not clicked:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="GREETING_TRIGGER_NOT_VISIBLE",
                    )
                performed = True
                if command.delivery_mode == "PLATFORM_DEFAULT":
                    expected = command.expected_platform_content
                    for _ in range(30):
                        observed = page.text(selectors.platform_greeting_message)
                        dialog = page.text(selectors.platform_greeting_dialog)
                        if observed and dialog and "已发送" in dialog:
                            evidence = hashlib.sha256(
                                f"{page.url}:{observed}:{command.model_dump_json()}".encode()
                            ).hexdigest()
                            if not expected or observed == expected:
                                return ExecutionResult(
                                    outcome=ExecutionOutcome.SUCCEEDED,
                                    evidence_hash=evidence,
                                    observed_content=observed,
                                )
                            return ExecutionResult(
                                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                                error_code="PLATFORM_DEFAULT_CONTENT_MISMATCH",
                                evidence_hash=evidence,
                                observed_content=observed,
                            )
                        time.sleep(0.1)
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="PLATFORM_DEFAULT_RESULT_NOT_OBSERVED",
                    )
                for _ in range(30):
                    if page.exists(selectors.message_composer):
                        break
                    time.sleep(0.1)
                else:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="COMPOSER_NOT_OBSERVED_AFTER_GREETING_TRIGGER",
                    )
                filled = page._evaluate(
                    "(() => { const element = document.querySelector("
                    f"{json.dumps(selectors.message_composer)}"
                    f"); if (!element) return false; const value = {json.dumps(command.content)}; "
                    "element.focus(); if ('value' in element) { "
                    "const prototype = element.tagName === 'TEXTAREA' "
                    "? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; "
                    "const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set; "
                    "if (setter) setter.call(element, value); else element.value = value; "
                    "} else { element.textContent = value; } "
                    "element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: "
                    "'insertText', data: value})); "
                    "element.dispatchEvent(new Event('change', {bubbles: true})); return true; })()"
                )
                if not filled:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="COMPOSER_FILL_NOT_CONFIRMED",
                    )
                sent = page._evaluate(
                    "(() => { const element = Array.from(document.querySelectorAll("
                    f"{json.dumps(selectors.message_send_button)}"
                    ")).find(item => item.getClientRects().length > 0); "
                    "if (!element) return false; element.click(); return true; })()"
                )
                if not sent:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="SEND_BUTTON_NOT_OBSERVED",
                    )
                for _ in range(30):
                    observed = page._evaluate(
                        "Array.from(document.querySelectorAll("
                        f"{json.dumps(selectors.sent_message_items)}"
                        f")).some(item => (item.textContent || '').includes("
                        f"{json.dumps(command.content)}))"
                    )
                    if observed:
                        evidence = hashlib.sha256(
                            f"{page.url}:{command.model_dump_json()}".encode()
                        ).hexdigest()
                        return ExecutionResult(
                            outcome=ExecutionOutcome.SUCCEEDED,
                            evidence_hash=evidence,
                        )
                    time.sleep(0.1)
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="RESULT_NOT_OBSERVED",
                )
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=(
                    ExecutionOutcome.OUTCOME_UNKNOWN
                    if performed
                    else ExecutionOutcome.FAILED_RETRYABLE
                ),
                error_code="RAW_CDP_ACTION_ERROR",
            )

    def execute_on_browser(
        self,
        browser: Browser,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        """按已批准目标选择唯一标签页，避免多标签环境依赖焦点或页面顺序。"""
        platform = Platform(command.platform)
        selectors = self.config.platforms[platform.value]
        matches: list[Page] = []
        for page in [item for context in browser.contexts for item in context.pages]:
            try:
                check = extract_current_page(
                    PlaywrightPageReader(page),
                    platform,
                    selectors,
                    self.config.version,
                )
            except PlaywrightError:
                continue
            if check.status is not SessionStatus.SESSION_READY:
                continue
            if command.action_type == "GREETING" and check.job:
                if (
                    command.company in check.job.company_name
                    and command.job_title in check.job.title
                    and command.recruiter in (check.job.recruiter_name or "")
                    and (
                        not command.external_job_id
                        or check.job.external_job_id == command.external_job_id
                    )
                ):
                    matches.append(page)
            elif check.conversation:
                if (
                    check.conversation.external_conversation_id
                    == command.conversation_key
                    and command.recruiter in check.conversation.recruiter_name
                    and command.company in (check.conversation.company_name or "")
                    and command.job_title in (check.conversation.job_title or "")
                ):
                    matches.append(page)
        if not matches:
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="APPROVED_TARGET_PAGE_NOT_FOUND",
            )
        if len(matches) > 1:
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="APPROVED_TARGET_PAGE_AMBIGUOUS",
            )
        return self.execute_on_page(matches[0], command)

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
                if command.delivery_mode == "PLATFORM_DEFAULT":
                    expected = command.expected_platform_content
                    page.locator(selectors.platform_greeting_dialog).wait_for(
                        state="visible", timeout=3000
                    )
                    observed = page.locator(
                        selectors.platform_greeting_message
                    ).first.text_content()
                    evidence = hashlib.sha256(
                        f"{page.url}:{observed}:{command.model_dump_json()}".encode()
                    ).hexdigest()
                    if observed and (not expected or observed == expected):
                        return ExecutionResult(
                            outcome=ExecutionOutcome.SUCCEEDED,
                            evidence_hash=evidence,
                            observed_content=observed,
                        )
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="PLATFORM_DEFAULT_CONTENT_MISMATCH",
                        evidence_hash=evidence,
                        observed_content=observed,
                    )
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
