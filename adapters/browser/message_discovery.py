import json
import time
from datetime import UTC, datetime
from urllib.request import urlopen

from pydantic import BaseModel, Field

from adapters.browser.playwright_reader import RawCdpPageReader, validate_local_cdp_url
from packages.browser_worker.config import BrowserSelectorsConfig
from packages.browser_worker.extractor import (
    extract_conversation_list,
    extract_current_page,
)
from packages.browser_worker.models import (
    BrowserConversationSummary,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


class DiscoveredConversation(BaseModel):
    summary: BrowserConversationSummary
    detail: ReadResult | None = None
    job_detail: ReadResult | None = None
    reason_codes: list[str] = Field(default_factory=list)


class MessageDiscoveryBatch(BaseModel):
    platform: Platform
    partition: str
    scroll_position: int = Field(ge=0)
    next_cursor: str | None = None
    scanned_at: datetime
    items: list[DiscoveredConversation] = Field(default_factory=list)
    seen_message_keys: list[str] = Field(default_factory=list, max_length=500)
    exhausted: bool = False


class BossMessageDiscoveryAdapter:
    """只进行列表导航与读取，不生成回复，也不执行平台写操作。"""

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        self.config = config
        self.selectors = config.platforms[Platform.BOSS.value]

    def scan(
        self,
        cdp_url: str,
        *,
        partition: str = "UNREAD",
        scroll_position: int = 0,
        seen_message_keys: list[str] | None = None,
        limit: int = 20,
    ) -> MessageDiscoveryBatch:
        validate_local_cdp_url(cdp_url)
        websocket_url = self._find_list_target(cdp_url)
        with RawCdpPageReader(websocket_url) as page:
            listing = extract_conversation_list(
                page, Platform.BOSS, self.selectors, self.config.version
            )
            if listing.status is not SessionStatus.SESSION_READY:
                raise ValueError("BOSS 对话列表结构不可用")
            eligible = [
                item
                for item in listing.conversations
                if _matches_partition(item, partition)
            ]
            candidates = select_discovery_candidates(
                eligible,
                seen_message_keys or [],
                scroll_position=scroll_position,
                limit=limit,
            )
            discovered = [
                self._open_and_read(page, item, cdp_url) for item in candidates
            ]
            page._evaluate(
                "(() => { const element = document.querySelector("
                f"{json.dumps(self.selectors.conversation_list_root)}); "
                "if (!element) return false; element.scrollTop = element.scrollHeight; "
                "element.dispatchEvent(new Event('scroll', {bubbles: true})); return true; })()"
            )
            next_position = min(len(eligible), scroll_position + limit)
            updated_seen = [*(seen_message_keys or []), *[
                _message_key(item) for item in candidates
            ]]
            exhausted = next_position >= len(eligible) and not listing.cursor
            return MessageDiscoveryBatch(
                platform=Platform.BOSS,
                partition=partition,
                scroll_position=next_position,
                next_cursor=listing.cursor,
                scanned_at=datetime.now(UTC),
                items=discovered,
                seen_message_keys=list(dict.fromkeys(updated_seen))[-500:],
                exhausted=exhausted,
            )

    def _find_list_target(self, cdp_url: str) -> str:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read())
        matches: list[str] = []
        for target in targets:
            websocket_url = target.get("webSocketDebuggerUrl")
            if target.get("type") != "page" or not websocket_url:
                continue
            try:
                with RawCdpPageReader(str(websocket_url)) as page:
                    if page.exists(self.selectors.conversation_list_root):
                        matches.append(str(websocket_url))
            except (OSError, TimeoutError, ValueError):
                continue
        if len(matches) != 1:
            raise ValueError(
                "未找到唯一 BOSS 消息列表页"
                if not matches
                else "检测到多个 BOSS 消息列表页"
            )
        return matches[0]

    def _open_and_read(
        self,
        page: RawCdpPageReader,
        summary: BrowserConversationSummary,
        cdp_url: str,
    ) -> DiscoveredConversation:
        clicked = page._evaluate(
            "(() => { const selector = "
            f"{json.dumps(self.selectors.conversation_list_items)}; "
            f"const attribute = {json.dumps(self.selectors.conversation_list_item_id_attribute)}; "
            f"const expected = {json.dumps(summary.external_conversation_id)}; "
            "const matches = Array.from(document.querySelectorAll(selector)).filter("
            "item => (item.getAttribute(attribute) || item.getAttribute('d-c')) === expected); "
            "if (matches.length !== 1 || matches[0].getClientRects().length === 0) return false; "
            "matches[0].click(); return true; })()"
        )
        if not clicked:
            return DiscoveredConversation(
                summary=summary,
                reason_codes=["CONVERSATION_LIST_ITEM_NOT_UNIQUE_OR_VISIBLE"],
            )
        for _ in range(30):
            detail = extract_current_page(
                page,
                Platform.BOSS,
                self.selectors,
                self.config.version,
                expected_recruiter=summary.recruiter_name,
            )
            if detail.page_type is PageType.CONVERSATION and detail.conversation:
                reasons = _verify_target(summary, detail)
                job_detail = (
                    self._read_linked_job(page, cdp_url)
                    if not reasons
                    and not (
                        summary.external_job_id
                        or detail.conversation.external_job_id
                    )
                    else None
                )
                return DiscoveredConversation(
                    summary=summary,
                    detail=detail if not reasons else None,
                    job_detail=job_detail,
                    reason_codes=reasons,
                )
            time.sleep(0.1)
        return DiscoveredConversation(
            summary=summary,
            reason_codes=["CONVERSATION_DETAIL_NOT_READY"],
        )

    def _read_linked_job(
        self, page: RawCdpPageReader, cdp_url: str
    ) -> ReadResult | None:
        href = page.attribute(self.selectors.conversation_job_link, "href")
        if not href:
            return None
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            before = {str(item.get("id")) for item in json.loads(response.read())}
        opened = page._evaluate(f"Boolean(window.open({json.dumps(href)}, '_blank'))")
        if not opened:
            return None
        for _ in range(30):
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            for target in targets:
                if str(target.get("id")) in before or not target.get("webSocketDebuggerUrl"):
                    continue
                try:
                    with RawCdpPageReader(str(target["webSocketDebuggerUrl"])) as job_page:
                        try:
                            result = extract_current_page(
                                job_page,
                                Platform.BOSS,
                                self.selectors,
                                self.config.version,
                            )
                        finally:
                            job_page._evaluate("window.close()")
                    return result if result.page_type is PageType.JOB else None
                except (OSError, TimeoutError, ValueError):
                    continue
            time.sleep(0.1)
        return None


def _verify_target(
    summary: BrowserConversationSummary, detail: ReadResult
) -> list[str]:
    conversation = detail.conversation
    if conversation is None:
        return ["CONVERSATION_DETAIL_MISSING"]
    if conversation.external_conversation_id != summary.external_conversation_id:
        return ["CONVERSATION_ID_MISMATCH"]
    if conversation.recruiter_name != summary.recruiter_name:
        return ["RECRUITER_TARGET_MISMATCH"]
    if summary.job_title and conversation.job_title != summary.job_title:
        return ["CONVERSATION_JOB_CHANGED"]
    if summary.company_name and conversation.company_name != summary.company_name:
        return ["CONVERSATION_COMPANY_MISMATCH"]
    if (
        summary.external_job_id
        and conversation.external_job_id
        and summary.external_job_id != conversation.external_job_id
    ):
        return ["CONVERSATION_JOB_ID_MISMATCH"]
    return []


def select_discovery_candidates(
    items: list[BrowserConversationSummary],
    seen_message_keys: list[str],
    *,
    scroll_position: int,
    limit: int,
) -> list[BrowserConversationSummary]:
    """列表重排后仍按最后消息去重，同时保留虚拟滚动位置。"""
    seen = set(seen_message_keys)
    window = items[scroll_position : scroll_position + limit]
    return [item for item in window if _message_key(item) not in seen]


def _message_key(item: BrowserConversationSummary) -> str:
    return f"{item.external_conversation_id}:{item.last_message_id or 'UNKNOWN'}"


def _matches_partition(
    item: BrowserConversationSummary, partition: str
) -> bool:
    if partition == "ALL":
        return True
    if partition == "NEW_GREETING":
        return item.category == "NEW_GREETING"
    return item.unread_count > 0
