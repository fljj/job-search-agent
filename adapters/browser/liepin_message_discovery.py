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
        self._hold_drawer_for_actions = False
        self._held_target: str | None = None
        self._held_opened_by_agent = False

    def hold_drawer_for_actions(self) -> None:
        """消息扫描成功后保留抽屉，供当前批次的已授权动作使用。"""

        self._hold_drawer_for_actions = True

    def _prepare_conversation_detail(self, page: RawCdpPageReader) -> None:
        """从猎聘 React 消息对象提取平台 ID、时间和方向供通用读取器使用。"""
        page._evaluate(
            "(() => { let annotated = 0;"
            "for (const wrapper of document.querySelectorAll('.im-ui-message-item-wrapper')) {"
            "const fiberKey = Object.getOwnPropertyNames(wrapper)"
            ".find(key => key.startsWith('__reactInternalInstance') || "
            "key.startsWith('__reactFiber'));"
            "let fiber = fiberKey ? wrapper[fiberKey] : null; let message = null;"
            "for (let depth = 0; fiber && depth < 8; depth += 1, fiber = fiber.return) {"
            "if (fiber.memoizedProps?.message) { message = fiber.memoizedProps.message; break; }"
            "}"
            "const target = wrapper.querySelector('.im-ui-message-item-body');"
            "if (!target || !message?.msgId || !message?.msgTime) continue;"
            "target.setAttribute('data-message-id', String(message.msgId));"
            "target.setAttribute('data-sent-at', new Date(Number(message.msgTime)).toISOString());"
            "target.setAttribute('data-direction', String(message.direction) === '0' ? 'outbound' : 'inbound');"
            "annotated += 1;"
            "} return annotated; })()"
        )

    def finish_actions(self) -> None:
        """动作批次结束后只收起本适配器打开的抽屉。"""

        target = self._held_target
        opened_by_agent = self._held_opened_by_agent
        self._held_target = None
        self._held_opened_by_agent = False
        self._hold_drawer_for_actions = False
        if target and opened_by_agent:
            self._restore_home(target)
            self.home_ready_for_job_discovery = True

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
            result = super().scan(
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
        except Exception:
            if opened_by_agent:
                self._restore_home(target)
                self.home_ready_for_job_discovery = True
            raise
        if self._hold_drawer_for_actions:
            self._held_target = target
            self._held_opened_by_agent = opened_by_agent
        elif opened_by_agent:
            self._restore_home(target)
            self.home_ready_for_job_discovery = True
        return result

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
                entry_selector = self.selectors.conversation_entry_button
                if not entry_selector:
                    raise ValueError("猎聘消息入口缺少选择器")
                opened_by_agent = bool(
                    page._evaluate(
                        "(() => { const matches = Array.from(document.querySelectorAll("
                        f"{json.dumps(entry_selector)}"
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
            if page.exists(self.selectors.conversation_root):
                dialog_close_selector = (
                    self.selectors.conversation_dialog_close_button
                )
                if not dialog_close_selector:
                    raise LiepinHomeRestoreError("猎聘会话弹窗缺少关闭选择器")
                dialog_closed = page._evaluate(
                    "(() => { const matches = Array.from(document.querySelectorAll("
                    f"{json.dumps(dialog_close_selector)}"
                    ")).filter(item => item.getClientRects().length > 0);"
                    "if (matches.length !== 1) return false; "
                    "matches[0].click(); return true; })()"
                )
                if not dialog_closed:
                    raise LiepinHomeRestoreError(
                        "猎聘会话弹窗关闭按钮不唯一或不可见"
                    )
                for _ in range(30):
                    if not page.exists(self.selectors.conversation_root):
                        break
                    time.sleep(0.1)
                else:
                    raise LiepinHomeRestoreError("猎聘会话弹窗关闭后仍然可见")
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
