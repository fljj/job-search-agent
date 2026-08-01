import json
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from adapters.browser.message_discovery import (
    MessageDiscoveryAdapter,
    MessageDiscoveryBatch,
)
from adapters.browser.playwright_reader import RawCdpPageReader, validate_local_cdp_url
from packages.browser_worker.config import BrowserSelectorsConfig
from packages.browser_worker.extractor import extract_conversation_list, extract_current_page
from packages.browser_worker.models import PageType, Platform, SessionStatus


class LiepinHomeRestoreError(ValueError):
    """消息读取后无法安全恢复猎聘首页。"""


class LiepinMessageDiscoveryAdapter(MessageDiscoveryAdapter):
    """在猎聘唯一首页中串行读取消息抽屉，并恢复 Agent 打开前的页面状态。"""

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.LIEPIN, config)
        self.home_ready_for_job_discovery = False

    def scan(
        self,
        cdp_url: str,
        *,
        partition: str = "UNREAD",
        scroll_position: int = 0,
        seen_message_keys: list[str] | None = None,
        priority_conversation_ids: list[str] | None = None,
        excluded_conversation_ids: list[str] | None = None,
        terminal_message_ids: dict[str, str] | None = None,
        known_linked_job_ids: dict[str, str] | None = None,
        limit: int = 20,
    ) -> MessageDiscoveryBatch:
        validate_local_cdp_url(cdp_url)
        self.home_ready_for_job_discovery = False
        target = self._find_home_target(cdp_url)
        opened_by_agent = self._ensure_drawer_open(target)
        try:
            return super().scan(
                cdp_url,
                partition=partition,
                scroll_position=scroll_position,
                seen_message_keys=seen_message_keys,
                priority_conversation_ids=priority_conversation_ids,
                excluded_conversation_ids=excluded_conversation_ids,
                terminal_message_ids=terminal_message_ids,
                known_linked_job_ids=known_linked_job_ids,
                limit=limit,
            )
        finally:
            if opened_by_agent:
                self._restore_home(target)
                self.home_ready_for_job_discovery = True

    def _find_home_target(self, cdp_url: str) -> str:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read())
        matches = [
            str(target["webSocketDebuggerUrl"])
            for target in targets
            if target.get("type") == "page"
            and target.get("webSocketDebuggerUrl")
            and urlparse(str(target.get("url") or "")).hostname == "c.liepin.com"
            and urlparse(str(target.get("url") or "")).path in {"", "/"}
        ]
        if len(matches) != 1:
            raise ValueError(
                "未找到唯一猎聘首页" if not matches else "检测到多个猎聘首页"
            )
        return matches[0]

    def _ensure_drawer_open(self, target: str) -> bool:
        opened_by_agent = False
        try:
            with RawCdpPageReader(target) as page:
                if page.exists(self.selectors.conversation_list_root):
                    self._verify_drawer_ready(page)
                    return False
                state = extract_current_page(
                    page,
                    Platform.LIEPIN,
                    self.selectors,
                    self.selectors.version,
                )
                if (
                    state.status is not SessionStatus.SESSION_READY
                    or state.page_type is not PageType.JOB_LIST
                ):
                    reason = (
                        state.reason_codes[0]
                        if state.reason_codes
                        else "UNKNOWN"
                    )
                    raise ValueError(
                        f"猎聘首页不能安全打开消息抽屉: {reason}"
                    )
                opened_by_agent = bool(
                    page._evaluate(
                        "(() => { const matches = Array.from(document.querySelectorAll("
                        f"{json.dumps(self.selectors.login_marker)}"
                        ")).filter(item => item.getClientRects().length > 0);"
                        "if (matches.length !== 1) return false;"
                        "matches[0].click(); return true; })()"
                    )
                )
                if not opened_by_agent:
                    raise ValueError("猎聘消息入口不唯一或不可见")
                for _ in range(30):
                    if page.exists(self.selectors.conversation_list_root):
                        self._verify_drawer_ready(page)
                        return True
                    time.sleep(0.1)
                raise ValueError("猎聘消息抽屉打开后未就绪")
        except ValueError:
            if opened_by_agent:
                self._restore_home(target)
            raise

    def _verify_drawer_ready(self, page: RawCdpPageReader) -> None:
        page_state = extract_current_page(
            page,
            Platform.LIEPIN,
            self.selectors,
            self.selectors.version,
        )
        if page_state.status is not SessionStatus.SESSION_READY:
            reason = (
                page_state.reason_codes[0]
                if page_state.reason_codes
                else "UNKNOWN"
            )
            raise ValueError(f"猎聘消息抽屉不能安全读取: {reason}")
        result = extract_conversation_list(
            page,
            Platform.LIEPIN,
            self.selectors,
            self.selectors.version,
        )
        if (
            result.status is not SessionStatus.SESSION_READY
            or result.page_type is not PageType.CONVERSATION_LIST
        ):
            reason = result.reason_codes[0] if result.reason_codes else "UNKNOWN"
            raise ValueError(f"猎聘消息抽屉不可用: {reason}")

    def _restore_home(self, target: str) -> None:
        close_selector = self.selectors.conversation_drawer_close_button
        if not close_selector:
            raise LiepinHomeRestoreError("猎聘消息抽屉缺少关闭选择器")
        with RawCdpPageReader(target) as page:
            state = extract_current_page(
                page,
                Platform.LIEPIN,
                self.selectors,
                self.selectors.version,
            )
            if state.status is SessionStatus.SESSION_PAUSED:
                reason = state.reason_codes[0] if state.reason_codes else "UNKNOWN"
                raise LiepinHomeRestoreError(
                    f"猎聘页面存在用户交互，不能关闭消息抽屉: {reason}"
                )
            closed = page._evaluate(
                "(() => { const matches = Array.from(document.querySelectorAll("
                f"{json.dumps(close_selector)}"
                ")).filter(item => item.getClientRects().length > 0);"
                "if (matches.length !== 1) return false; matches[0].click(); return true; })()"
            )
            if not closed:
                raise LiepinHomeRestoreError(
                    "猎聘消息抽屉关闭按钮不唯一或不可见"
                )
            for _ in range(30):
                if (
                    not page.exists(self.selectors.conversation_list_root)
                    and page.exists(self.selectors.job_list_root)
                ):
                    return
                time.sleep(0.1)
        raise LiepinHomeRestoreError("猎聘消息抽屉关闭后未恢复首页")
