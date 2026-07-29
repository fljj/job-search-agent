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

from adapters.browser.maimai_recommendations import (
    MaimaiRecommendationAdapter,
    MaimaiRecommendationCard,
)
from adapters.browser.playwright_reader import (
    PlaywrightPageReader,
    RawCdpPageReader,
    validate_local_cdp_url,
)
from apps.api.app.core.recommendation_config import get_recommendation_rules
from packages.browser_worker.actions import ApprovedCommand, ExecutionOutcome, ExecutionResult
from packages.browser_worker.config import BrowserSelectorsConfig, PlatformSelectors
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import Platform, ReadResult, SessionStatus


def _conversation_key_matches(actual: str, expected: str | None) -> bool:
    if not expected:
        return False
    return expected.startswith("derived:") or actual == expected


def _reply_target_matches(
    result: ReadResult,
    command: ApprovedCommand,
) -> bool:
    conversation = result.conversation
    return bool(
        result.status is SessionStatus.SESSION_READY
        and conversation
        and _conversation_key_matches(
            conversation.external_conversation_id,
            command.conversation_key,
        )
        and command.recruiter in conversation.recruiter_name
        and command.job_title in (conversation.job_title or "")
    )


def _recommendation_card(command: ApprovedCommand) -> MaimaiRecommendationCard:
    if not command.conversation_key:
        raise ValueError("推荐动作缺少平台推荐 ID")
    return MaimaiRecommendationCard(
        external_recommendation_id=command.conversation_key,
        recruiter_name=command.recruiter,
        recruiter_title="",
        company_name=command.company,
        job_title=command.job_title,
        description_summary=command.content,
        card_text=command.content or command.job_title,
    )


class PlaywrightActionExecutor:
    """仅执行已由服务端原子批准的单个动作。"""

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        self.config = config

    def execute(self, cdp_url: str, command: ApprovedCommand) -> ExecutionResult:
        validate_local_cdp_url(cdp_url)
        if command.action_type in {
            "PLATFORM_RECOMMENDATION_ACCEPT",
            "PLATFORM_RECOMMENDATION_REJECT",
        }:
            return MaimaiRecommendationAdapter().execute(
                cdp_url,
                _recommendation_card(command),
                accept=command.action_type.endswith("ACCEPT"),
                rules=get_recommendation_rules(),
            )
        if command.action_type == "GREETING" and command.platform == "TELEGRAM":
            return self._execute_telegram_greeting(cdp_url, command)
        if command.action_type == "GREETING":
            return self._execute_greeting_over_raw_cdp(cdp_url, command)
        if command.action_type in {
            "RESUME_CONSENT_ACCEPT",
            "CONTACT_CONSENT_ACCEPT",
            "LOCATION_CONSENT_ACCEPT",
        }:
            return self._execute_platform_consent_over_raw_cdp(cdp_url, command)
        if command.action_type in {"REPLY", "LOW_SCORE_DECLINE", "MISMATCH_DECLINE"}:
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

    def _execute_telegram_greeting(
        self,
        cdp_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        if not command.content or not command.recruiter.startswith("@"):
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_FINAL,
                error_code="TELEGRAM_TARGET_INVALID",
            )
        performed = False
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            matches = [
                str(item["webSocketDebuggerUrl"])
                for item in targets
                if item.get("type") == "page"
                and str(item.get("url") or "").startswith("https://web.telegram.org/a/")
                and item.get("webSocketDebuggerUrl")
            ]
            if len(matches) != 1:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code="TELEGRAM_PAGE_NOT_UNIQUE",
                )
            username = command.recruiter.removeprefix("@")
            with RawCdpPageReader(matches[0]) as page:
                contact_opened = self._open_telegram_contact(page, username)
                existing_draft = self._telegram_composer_content(page)
                if not contact_opened and existing_draft != command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="TELEGRAM_CONTACT_NOT_READY",
                    )
                already_sent = page._evaluate(
                    "[...document.querySelectorAll('.Message.own')].some("
                    "item => (item.innerText || '').includes("
                    f"{json.dumps(command.content)}))"
                )
                if already_sent:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.SUCCEEDED,
                        observed_content=command.content,
                    )
                composer = self._telegram_element_point(
                    page,
                    "[contenteditable=true][role=textbox][aria-label=Message]",
                )
                if composer is None:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="TELEGRAM_COMPOSER_FILL_FAILED",
                    )
                composer_content = self._telegram_composer_content(page)
                if composer_content and composer_content != command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="TELEGRAM_COMPOSER_NOT_EMPTY",
                    )
                if not composer_content:
                    self._trusted_click(page, composer)
                    page._command("Input.insertText", {"text": command.content})
                    if not page._evaluate(
                        "(() => { const element = document.querySelector("
                        "'[contenteditable=true][role=textbox][aria-label=Message]');"
                        f"return (element?.textContent || '').trim() === {json.dumps(command.content)};"
                        "})()"
                    ):
                        return ExecutionResult(
                            outcome=ExecutionOutcome.FAILED_RETRYABLE,
                            error_code="TELEGRAM_COMPOSER_FILL_FAILED",
                        )
                    performed = True
                send_button = None
                for _ in range(50):
                    send_button = self._telegram_element_point(
                        page,
                        "button.send.main-button, button.Button.send, "
                        "button[aria-label='Send Message']",
                    )
                    if send_button is not None:
                        break
                    time.sleep(0.1)
                if send_button is None:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="TELEGRAM_SEND_BUTTON_NOT_READY",
                    )
                performed = True
                self._trusted_click(page, send_button)
                for _ in range(50):
                    observed = page._evaluate(
                        "[...document.querySelectorAll('.Message.own')].some("
                        "item => (item.innerText || '').includes("
                        f"{json.dumps(command.content)}))"
                    )
                    if observed:
                        return ExecutionResult(
                            outcome=ExecutionOutcome.SUCCEEDED,
                            external_reference=f"@{username}",
                            observed_content=command.content,
                        )
                    time.sleep(0.1)
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="TELEGRAM_RESULT_NOT_OBSERVED",
                )
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=(
                    ExecutionOutcome.OUTCOME_UNKNOWN
                    if performed
                    else ExecutionOutcome.FAILED_RETRYABLE
                ),
                error_code="TELEGRAM_CDP_ERROR",
            )

    @staticmethod
    def _open_telegram_contact(
        page: RawCdpPageReader,
        username: str,
    ) -> bool:
        search_ready = page._evaluate(
            "(() => { const element = [...document.querySelectorAll("
            "\"input[placeholder='Search']\")].find(item => "
            "item.getClientRects().length > 0);"
            "if (!element) return false;"
            "const setter = Object.getOwnPropertyDescriptor("
            "HTMLInputElement.prototype, 'value').set;"
            "setter.call(element, '');"
            "element.dispatchEvent(new Event('input', {bubbles:true}));"
            "element.focus(); return true; })()"
        )
        if not search_ready:
            return False
        page._command("Input.insertText", {"text": f"@{username}"})
        expected = username.lower()
        for _ in range(50):
            point = page._evaluate(
                "(() => { const handle = [...document.querySelectorAll('.handle')].find("
                "item => (item.textContent || '').trim().toLowerCase()"
                f".replace(/^@/, '') === {json.dumps(expected)}"
                "); const element = handle?.closest('[role=button], .ListItem-button');"
                "if (!element) return null; const rect = element.getBoundingClientRect();"
                "return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}; })()"
            )
            if isinstance(point, dict):
                PlaywrightActionExecutor._trusted_click(page, point)
                break
            time.sleep(0.1)
        else:
            return False
        for _ in range(50):
            if page._evaluate(
                "Boolean(document.querySelector("
                "'[contenteditable=true][role=textbox][aria-label=Message]'))"
            ):
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _telegram_element_point(
        page: RawCdpPageReader,
        selector: str,
    ) -> dict[str, object] | None:
        point = page._evaluate(
            "(() => { const element = [...document.querySelectorAll("
            f"{json.dumps(selector)})].find(item => item.getClientRects().length > 0);"
            "if (!element || element.disabled) return null;"
            "const rect = element.getBoundingClientRect();"
            "return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}; })()"
        )
        return point if isinstance(point, dict) else None

    @staticmethod
    def _telegram_composer_content(page: RawCdpPageReader) -> str:
        content = page._evaluate(
            "(() => { const element = document.querySelector("
            "'[contenteditable=true][role=textbox][aria-label=Message]');"
            "return (element?.textContent || '').trim();"
            "})()"
        )
        return content if isinstance(content, str) else ""

    @staticmethod
    def _trusted_click(
        page: RawCdpPageReader,
        point: dict[str, object],
    ) -> None:
        x = point.get("x")
        y = point.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError("CDP 点击坐标无效")
        coordinates = {
            "x": float(x),
            "y": float(y),
            "button": "left",
            "clickCount": 1,
        }
        page._command(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", **coordinates},
        )
        page._command(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", **coordinates},
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

    def _execute_platform_consent_over_raw_cdp(
        self,
        cdp_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        allowed_prompts = {
            "我想要一份您的附件简历，您是否同意",
            "我想要和您交换联系方式，您是否同意",
        }
        location_consent = command.action_type == "LOCATION_CONSENT_ACCEPT"
        if (
            command.platform != "BOSS"
            or (location_consent and not command.content)
            or (
                not location_consent
                and command.content not in allowed_prompts
            )
        ):
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_FINAL,
                error_code="PLATFORM_CONSENT_NOT_ALLOWED",
            )
        selectors = self.config.platforms[command.platform]
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            matches: list[str] = []
            for target in targets:
                websocket_url = target.get("webSocketDebuggerUrl")
                if target.get("type") != "page" or not websocket_url:
                    continue
                with RawCdpPageReader(str(websocket_url)) as page:
                    check = extract_current_page(
                        page,
                        Platform.BOSS,
                        selectors,
                        self.config.version,
                    )
                    if not _reply_target_matches(check, command):
                        if not page.exists(selectors.conversation_list_root):
                            continue
                        if not self._open_approved_conversation(page, selectors, command):
                            continue
                        for _ in range(30):
                            check = extract_current_page(
                                page,
                                Platform.BOSS,
                                selectors,
                                self.config.version,
                            )
                            if _reply_target_matches(check, command):
                                break
                            time.sleep(0.1)
                        else:
                            continue
                    matches.append(str(websocket_url))
            if len(matches) != 1:
                return ExecutionResult(
                    outcome=ExecutionOutcome.FAILED_RETRYABLE,
                    error_code=(
                        "APPROVED_TARGET_PAGE_NOT_FOUND"
                        if not matches
                        else "APPROVED_TARGET_PAGE_AMBIGUOUS"
                    ),
                )
            if location_consent:
                return self._accept_location_consent(matches[0], command)
            return self._accept_platform_consent(matches[0], command)
        except (OSError, TimeoutError, ValueError):
            return ExecutionResult(
                outcome=ExecutionOutcome.FAILED_RETRYABLE,
                error_code="RAW_CDP_PREFLIGHT_ERROR",
            )

    def _accept_platform_consent(
        self,
        websocket_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        performed = False
        try:
            with RawCdpPageReader(websocket_url) as page:
                state = self._platform_consent_state(page, command.content or "")
                if state == "ACCEPTED":
                    return ExecutionResult(
                        outcome=ExecutionOutcome.SUCCEEDED,
                        observed_content=command.content,
                    )
                if state != "PENDING":
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="PLATFORM_CONSENT_CARD_NOT_FOUND",
                    )
                point = page._evaluate(
                    "(() => {"
                    f"const prompt={json.dumps(command.content)};"
                    "const popovers=[...document.querySelectorAll('.respond-popover')];"
                    "const popover=popovers.find(item => "
                    "(item.innerText||'').includes(prompt));"
                    "const agree=popover && [...popover.querySelectorAll('.btn-agree')]"
                    ".find(item => item.getClientRects().length>0 "
                    "&& (item.textContent||'').trim()==='同意');"
                    "if(!agree)return null;const r=agree.getBoundingClientRect();"
                    "return {x:r.x+r.width/2,y:r.y+r.height/2};"
                    "})()"
                )
                if not isinstance(point, dict):
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="PLATFORM_CONSENT_BUTTON_NOT_READY",
                    )
                performed = True
                self._trusted_click(page, point)
                for _ in range(30):
                    if self._platform_consent_state(page, command.content or "") == "ACCEPTED":
                        return ExecutionResult(
                            outcome=ExecutionOutcome.SUCCEEDED,
                            evidence_hash=hashlib.sha256(
                                f"{page.url}:{command.content}".encode()
                            ).hexdigest(),
                            observed_content=command.content,
                        )
                    time.sleep(0.1)
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="PLATFORM_CONSENT_RESULT_NOT_OBSERVED",
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

    def _accept_location_consent(
        self,
        websocket_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        performed = False
        try:
            with RawCdpPageReader(websocket_url) as page:
                state = self._location_consent_state(
                    page,
                    command.content or "",
                )
                if state == "ACCEPTED":
                    return ExecutionResult(
                        outcome=ExecutionOutcome.SUCCEEDED,
                        observed_content=command.content,
                    )
                if state != "PENDING":
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="LOCATION_CONSENT_CARD_NOT_FOUND",
                    )
                point = page._evaluate(
                    "(() => {"
                    f"const address={json.dumps(command.content)};"
                    "const cards=[...document.querySelectorAll('.msg-dialog-position')];"
                    "const card=cards.find(item => "
                    "(item.querySelector('.msg-dialog-title')?.textContent||'').trim()"
                    "==='您是否接受此工作地点?' && "
                    "((item.querySelector('.msg-dialog-desc')?.getAttribute('aria-label')"
                    "||item.querySelector('.msg-dialog-desc')?.textContent||'').trim())"
                    "===address);"
                    "const accept=card && [...card.querySelectorAll("
                    "'.msg-dialog-footer-v2 .btn-light-v2')].find(item => "
                    "item.getClientRects().length>0 && "
                    "(item.textContent||'').trim()==='可以接受');"
                    "if(!accept)return null;const r=accept.getBoundingClientRect();"
                    "return {x:r.x+r.width/2,y:r.y+r.height/2};"
                    "})()"
                )
                if not isinstance(point, dict):
                    return ExecutionResult(
                        outcome=ExecutionOutcome.FAILED_RETRYABLE,
                        error_code="LOCATION_CONSENT_BUTTON_NOT_READY",
                    )
                performed = True
                self._trusted_click(page, point)
                for _ in range(30):
                    state = self._location_consent_state(
                        page,
                        command.content or "",
                    )
                    if state in {"ACCEPTED", "MISSING"}:
                        return ExecutionResult(
                            outcome=ExecutionOutcome.SUCCEEDED,
                            evidence_hash=hashlib.sha256(
                                f"{page.url}:{command.content}".encode()
                            ).hexdigest(),
                            observed_content=command.content,
                        )
                    time.sleep(0.1)
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="LOCATION_CONSENT_RESULT_NOT_OBSERVED",
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

    @staticmethod
    def _location_consent_state(
        page: RawCdpPageReader,
        address: str,
    ) -> str:
        result = page._evaluate(
            "(() => {"
            f"const address={json.dumps(address)};"
            "const cards=[...document.querySelectorAll('.msg-dialog-position')];"
            "const card=cards.find(item => "
            "(item.querySelector('.msg-dialog-title')?.textContent||'').trim()"
            "==='您是否接受此工作地点?' && "
            "((item.querySelector('.msg-dialog-desc')?.getAttribute('aria-label')"
            "||item.querySelector('.msg-dialog-desc')?.textContent||'').trim())"
            "===address);"
            "if(!card)return 'MISSING';"
            "const accept=[...card.querySelectorAll("
            "'.msg-dialog-footer-v2 .btn-light-v2')].find(item => "
            "(item.textContent||'').trim()==='可以接受');"
            "if(!accept || accept.classList.contains('disabled'))return 'ACCEPTED';"
            "return 'PENDING';"
            "})()"
        )
        return str(result)

    @staticmethod
    def _platform_consent_state(
        page: RawCdpPageReader,
        prompt: str,
    ) -> str:
        result = page._evaluate(
            "(() => {"
            f"const prompt={json.dumps(prompt)};"
            "const cards=[...document.querySelectorAll("
            "'.message-dialog-both.message-card-wrap')];"
            "const card=cards.find(item => "
            "(item.querySelector('.message-card-top-title')?.textContent||'').trim()"
            "===prompt);"
            "if(!card)return 'MISSING';"
            "const agree=[...card.querySelectorAll('.card-btn')].find(item => "
            "(item.textContent||'').trim()==='同意');"
            "return agree?.classList.contains('disabled')?'ACCEPTED':'PENDING';"
            "})()"
        )
        return str(result)

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
                    check = extract_current_page(page, platform, selectors, self.config.version)
                    if _reply_target_matches(check, command):
                        matches.append(str(websocket_url))
                        continue
                    if not page.exists(selectors.conversation_list_root):
                        continue
                    if not self._open_approved_conversation(page, selectors, command):
                        continue
                    for _ in range(30):
                        check = extract_current_page(page, platform, selectors, self.config.version)
                        if _reply_target_matches(check, command):
                            matches.append(str(websocket_url))
                            break
                        time.sleep(0.1)
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

    @staticmethod
    def _open_approved_conversation(
        page: RawCdpPageReader,
        selectors: PlatformSelectors,
        command: ApprovedCommand,
    ) -> bool:
        item_selector = selectors.conversation_list_items
        id_attribute = selectors.conversation_list_item_id_attribute
        id_json_key = selectors.conversation_list_item_id_json_key
        recruiter_selector = selectors.conversation_list_item_recruiter
        job_selector = selectors.conversation_list_item_job_title
        company_selector = selectors.conversation_list_item_company
        return bool(
            page._evaluate(
                "(() => {"
                f"const items = [...document.querySelectorAll({json.dumps(item_selector)})];"
                f"const expectedId = {json.dumps(command.conversation_key)};"
                f"const expectedRecruiter = {json.dumps(command.recruiter)};"
                f"const expectedJob = {json.dumps(command.job_title)};"
                f"const expectedCompany = {json.dumps(command.company)};"
                f"const idAttribute = {json.dumps(id_attribute)};"
                f"const idJsonKey = {json.dumps(id_json_key)};"
                f"const recruiterSelector = {json.dumps(recruiter_selector)};"
                f"const jobSelector = {json.dumps(job_selector)};"
                f"const companySelector = {json.dumps(company_selector)};"
                "const visible = items.filter(item => item.getClientRects().length > 0);"
                "let matches = visible.filter(item => {"
                " const recruiter = item.querySelector(recruiterSelector)?.textContent?.trim() || '';"
                " if (recruiter !== expectedRecruiter) return false;"
                " if (expectedId?.startsWith('derived:')) return true;"
                " const raw = item.getAttribute(idAttribute) || item.getAttribute('d-c');"
                " if (!raw) return false;"
                " if (!idJsonKey) return raw === expectedId;"
                " try { return String(JSON.parse(raw)[idJsonKey]) === expectedId; }"
                " catch { return false; }"
                "});"
                "if (matches.length > 1) {"
                " const narrowed = matches.filter(item => {"
                "  const job = item.querySelector(jobSelector)?.textContent?.trim() || '';"
                "  const company = item.querySelector(companySelector)?.textContent?.trim() || '';"
                "  const jobMatches = !expectedJob || job.includes(expectedJob) || expectedJob.includes(job);"
                "  const companyMatches = !expectedCompany || company.includes(expectedCompany) || expectedCompany.includes(company);"
                "  return jobMatches && companyMatches;"
                " });"
                " if (narrowed.length === 1) matches = narrowed;"
                "}"
                "if (matches.length !== 1) return false;"
                "matches[0].click(); return true;"
                "})()"
            )
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
        if command.action_type == "GREETING" and command.platform == "TELEGRAM":
            return self._observe_telegram_greeting(cdp_url, command)
        if command.action_type in {
            "PLATFORM_RECOMMENDATION_ACCEPT",
            "PLATFORM_RECOMMENDATION_REJECT",
        }:
            return MaimaiRecommendationAdapter().observe(
                cdp_url,
                _recommendation_card(command),
                accept=command.action_type.endswith("ACCEPT"),
                rules=get_recommendation_rules(),
            )
        if command.action_type not in {
            "REPLY",
            "LOW_SCORE_DECLINE",
            "MISMATCH_DECLINE",
        }:
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
                    check = extract_current_page(page, platform, selectors, self.config.version)
                if (
                    check.status is SessionStatus.SESSION_READY
                    and check.conversation
                    and _conversation_key_matches(
                        check.conversation.external_conversation_id,
                        command.conversation_key,
                    )
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
                observed = bool(
                    page._evaluate(
                        "Array.from(document.querySelectorAll("
                        f"{json.dumps(selectors.sent_message_items)}"
                        f")).some(item => (item.textContent || '').includes("
                        f"{json.dumps(command.content)}))"
                    )
                )
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

    def _observe_telegram_greeting(
        self,
        cdp_url: str,
        command: ApprovedCommand,
    ) -> ExecutionResult:
        if not command.content or not command.recruiter.startswith("@"):
            return ExecutionResult(
                outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                error_code="TELEGRAM_TARGET_INVALID",
            )
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            matches = [
                str(item["webSocketDebuggerUrl"])
                for item in targets
                if item.get("type") == "page"
                and str(item.get("url") or "").startswith(
                    "https://web.telegram.org/a/"
                )
                and item.get("webSocketDebuggerUrl")
            ]
            if len(matches) != 1:
                return ExecutionResult(
                    outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                    error_code="TELEGRAM_PAGE_NOT_UNIQUE",
                )
            username = command.recruiter.removeprefix("@")
            with RawCdpPageReader(matches[0]) as page:
                contact_opened = self._open_telegram_contact(page, username)
                existing_draft = self._telegram_composer_content(page)
                observed = bool(
                    page._evaluate(
                        "[...document.querySelectorAll('.Message.own')].some("
                        "item => (item.innerText || '').includes("
                        f"{json.dumps(command.content)}))"
                    )
                )
                if observed:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.SUCCEEDED,
                        evidence_hash=hashlib.sha256(
                            f"{page.url}:{command.model_dump_json()}".encode()
                        ).hexdigest(),
                        observed_content=command.content,
                    )
                if not contact_opened and existing_draft != command.content:
                    return ExecutionResult(
                        outcome=ExecutionOutcome.OUTCOME_UNKNOWN,
                        error_code="TELEGRAM_CONTACT_NOT_READY",
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
                    _conversation_key_matches(
                        check.conversation.external_conversation_id,
                        command.conversation_key,
                    )
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
                if command.external_job_id and check.job.external_job_id != command.external_job_id:
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
                page.locator(selectors.message_composer).wait_for(state="visible", timeout=3000)
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
            elif not _conversation_key_matches(
                check.conversation.external_conversation_id,
                command.conversation_key,
            ):
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
                item = page.locator(selectors.resume_items).filter(has_text=command.attachment_name)
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
                return ExecutionResult(outcome=ExecutionOutcome.SUCCEEDED, evidence_hash=evidence)
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
