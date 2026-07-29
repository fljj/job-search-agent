import hashlib
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

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
from packages.policy_engine.recommendation import RecommendationRules


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


class MessageDiscoveryAdapter:
    """只进行列表导航与读取，不生成回复，也不执行平台写操作。"""

    def __init__(
        self, platform: Platform, config: BrowserSelectorsConfig
    ) -> None:
        self.platform = platform
        self.config = config
        self.selectors = config.platforms[platform.value]

    def scan(
        self,
        cdp_url: str,
        *,
        partition: str = "UNREAD",
        scroll_position: int = 0,
        seen_message_keys: list[str] | None = None,
        excluded_conversation_ids: list[str] | None = None,
        known_linked_job_ids: dict[str, str] | None = None,
        limit: int = 20,
    ) -> MessageDiscoveryBatch:
        validate_local_cdp_url(cdp_url)
        stable_seen_message_keys = [
            key
            for key in (seen_message_keys or [])
            if not key.endswith(":UNKNOWN")
        ]
        excluded = set(excluded_conversation_ids or [])
        websocket_url = self._find_list_target(cdp_url)
        with RawCdpPageReader(websocket_url) as page:
            listing = extract_conversation_list(
                page, self.platform, self.selectors, self.config.version
            )
            if listing.status is not SessionStatus.SESSION_READY:
                raise ValueError(f"{self.platform.value} 对话列表结构不可用")
            _normalize_duplicate_conversation_ids(listing.conversations)
            eligible = [
                item
                for item in listing.conversations
                if _matches_partition(item, partition)
                and self._include_summary(item)
                and item.external_conversation_id not in excluded
            ]
            candidates = select_discovery_candidates(
                eligible,
                stable_seen_message_keys,
                scroll_position=scroll_position,
                limit=limit,
            )
            linked_job_cache: dict[str, ReadResult | None] = {}
            discovered = [
                self._open_and_read(
                    page,
                    item,
                    cdp_url,
                    linked_job_cache=linked_job_cache,
                    known_linked_job_id=(
                        (known_linked_job_ids or {}).get(
                            item.external_conversation_id
                        )
                    ),
                )
                for item in candidates
            ]
            page._evaluate(
                "(() => { const element = document.querySelector("
                f"{json.dumps(self.selectors.conversation_list_root)}); "
                "if (!element) return false; element.scrollTop = element.scrollHeight; "
                "element.dispatchEvent(new Event('scroll', {bubbles: true})); return true; })()"
            )
            next_position = min(len(eligible), scroll_position + limit)
            updated_seen = [
                *stable_seen_message_keys,
                *_attempted_message_keys(candidates, discovered),
            ]
            exhausted = next_position >= len(eligible) and not listing.cursor
            return MessageDiscoveryBatch(
                platform=self.platform,
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
                    host = (urlparse(page.url).hostname or "").lower()
                    if (
                        host in self.selectors.allowed_hosts
                        and page.exists(self.selectors.conversation_list_root)
                    ):
                        matches.append(str(websocket_url))
            except (OSError, TimeoutError, ValueError):
                continue
        if len(matches) != 1:
            raise ValueError(
                f"未找到唯一 {self.platform.value} 消息列表页"
                if not matches
                else f"检测到多个 {self.platform.value} 消息列表页"
            )
        return matches[0]

    def _include_summary(self, _summary: BrowserConversationSummary) -> bool:
        return True

    def _open_and_read(
        self,
        page: RawCdpPageReader,
        summary: BrowserConversationSummary,
        cdp_url: str,
        *,
        linked_job_cache: dict[str, ReadResult | None] | None = None,
        known_linked_job_id: str | None = None,
    ) -> DiscoveredConversation:
        clicked = page._evaluate(
            "(() => { const selector = "
            f"{json.dumps(self.selectors.conversation_list_items)}; "
            f"const attribute = {json.dumps(self.selectors.conversation_list_item_id_attribute)}; "
            f"const jsonKey = {json.dumps(self.selectors.conversation_list_item_id_json_key)}; "
            f"const expected = {json.dumps(summary.external_conversation_id)}; "
            f"const recruiterSelector = {json.dumps(self.selectors.conversation_list_item_recruiter)}; "
            f"const expectedRecruiter = {json.dumps(summary.recruiter_name)}; "
            f"const jobSelector = {json.dumps(self.selectors.conversation_list_item_job_title)}; "
            f"const expectedJob = {json.dumps(summary.job_title)}; "
            "const matches = Array.from(document.querySelectorAll(selector)).filter("
            "item => { const raw = item.getAttribute(attribute) || item.getAttribute('d-c'); "
            "const recruiter = item.querySelector(recruiterSelector)?.textContent?.trim(); "
            "const job = item.querySelector(jobSelector)?.textContent?.trim(); "
            "if (expected.startsWith('derived:')) return recruiter === expectedRecruiter "
            "&& (!expectedJob || job === expectedJob); "
            "if (!raw) return false; if (!jsonKey) return raw === expected; "
            "try { return String(JSON.parse(raw)[jsonKey]) === expected; } "
            "catch { return false; } }); "
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
                self.platform,
                self.selectors,
                self.config.version,
                expected_recruiter=summary.recruiter_name,
            )
            if (
                detail.page_type is PageType.CONVERSATION
                and detail.conversation
                and detail.conversation.messages
            ):
                reasons = _verify_target(summary, detail)
                if not reasons and summary.external_conversation_id.startswith(
                    "derived:"
                ):
                    detail.conversation.external_conversation_id = (
                        summary.external_conversation_id
                    )
                linked_href = self._linked_job_href(page) if not reasons else None
                visible_job_id = _job_id_from_href(linked_href)
                reuse_linked_job = bool(
                    known_linked_job_id
                    and visible_job_id == known_linked_job_id
                )
                job_detail = None
                if not reasons and not reuse_linked_job:
                    job_detail = self._read_linked_job(
                        page,
                        cdp_url,
                        cache=linked_job_cache,
                    )
                if (
                    job_detail
                    and job_detail.job
                    and not detail.conversation.external_job_id
                ):
                    detail.conversation.external_job_id = (
                        job_detail.job.external_job_id
                    )
                elif reuse_linked_job:
                    detail.conversation.external_job_id = known_linked_job_id
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
        self,
        page: RawCdpPageReader,
        cdp_url: str,
        *,
        cache: dict[str, ReadResult | None] | None = None,
    ) -> ReadResult | None:
        href = self._linked_job_href(page)
        if not href:
            return None
        if (urlparse(href).hostname or "").lower() not in self.selectors.allowed_hosts:
            return None
        if cache is not None and href in cache:
            return cache[href]
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            before = {str(item.get("id")) for item in json.loads(response.read())}
        request = Request(
            f"{cdp_url.rstrip('/')}/json/new?{quote(href, safe=':/?=&%')}",
            method="PUT",
        )
        try:
            with urlopen(request, timeout=3) as response:
                created_target = json.loads(response.read())
        except (OSError, TimeoutError, ValueError):
            return None
        created_target_id = str(created_target.get("id") or "")
        opened_target_ids: set[str] = set()
        for _ in range(30):
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            for target in targets:
                target_id = str(target.get("id"))
                if (
                    target_id in before
                    or (created_target_id and target_id != created_target_id)
                    or not target.get("webSocketDebuggerUrl")
                ):
                    continue
                opened_target_ids.add(target_id)
                try:
                    with RawCdpPageReader(str(target["webSocketDebuggerUrl"])) as job_page:
                        if (
                            urlparse(job_page.url).hostname or ""
                        ).lower() not in self.selectors.allowed_hosts:
                            continue
                        result = extract_current_page(
                            job_page,
                            self.platform,
                            self.selectors,
                            self.config.version,
                        )
                        if (
                            result.status is SessionStatus.SESSION_READY
                            and result.page_type is PageType.JOB
                            and result.job is not None
                        ):
                            job_page._evaluate("window.close()")
                            if cache is not None:
                                cache[href] = result
                            return result
                except (OSError, TimeoutError, ValueError):
                    continue
            time.sleep(0.1)
        for target_id in opened_target_ids or {created_target_id}:
            if not target_id:
                continue
            self._close_target(cdp_url, target_id)
        if cache is not None:
            cache[href] = None
        return None

    def _linked_job_href(self, page: RawCdpPageReader) -> str | None:
        href = page.attribute(self.selectors.conversation_job_link, "href")
        if href:
            return urljoin(page.url, href)
        if self.platform is not Platform.BOSS:
            return None
        encrypted_job_id = page._evaluate(
            "document.querySelector('.chat-position-content')"
            "?.__vue__?.['conversation$']?.encryptJobId || null"
        )
        if not isinstance(encrypted_job_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_~-]+", encrypted_job_id
        ):
            return None
        return (
            "https://www.zhipin.com/job_detail/"
            f"{quote(encrypted_job_id, safe='_~-')}.html"
        )

    @staticmethod
    def _close_target(cdp_url: str, target_id: str) -> None:
        try:
            with urlopen(
                f"{cdp_url.rstrip('/')}/json/close/{target_id}", timeout=3
            ):
                pass
        except (OSError, TimeoutError, ValueError):
            pass


class BossMessageDiscoveryAdapter(MessageDiscoveryAdapter):
    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.BOSS, config)


class MaimaiMessageDiscoveryAdapter(MessageDiscoveryAdapter):
    """读取脉脉普通私信；系统推荐和官方账号仍由独立流程处理。"""

    def __init__(
        self,
        config: BrowserSelectorsConfig,
        recommendation_rules: RecommendationRules,
    ) -> None:
        super().__init__(Platform.MAIMAI, config)
        self.recommendation_rules = recommendation_rules

    def _include_summary(self, summary: BrowserConversationSummary) -> bool:
        if summary.recruiter_name in self.recommendation_rules.official_accounts:
            return False
        if summary.category in {"OFFICIAL", "RECOMMENDATION", "SYSTEM_RECOMMENDATION"}:
            return False
        preview = summary.last_message_text or ""
        return not any(
            marker in preview
            for marker in self.recommendation_rules.recommendation_markers
        )


def _verify_target(
    summary: BrowserConversationSummary, detail: ReadResult
) -> list[str]:
    conversation = detail.conversation
    if conversation is None:
        return ["CONVERSATION_DETAIL_MISSING"]
    if (
        not summary.external_conversation_id.startswith("derived:")
        and conversation.external_conversation_id != summary.external_conversation_id
    ):
        return ["CONVERSATION_ID_MISMATCH"]
    if conversation.recruiter_name != summary.recruiter_name:
        return ["RECRUITER_TARGET_MISMATCH"]
    if (
        not summary.external_conversation_id.startswith("derived:")
        and summary.job_title
        and conversation.job_title != summary.job_title
    ):
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


def _job_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r"/job_detail/([^/.]+)\.html", href)
    return match.group(1) if match else None


def _normalize_duplicate_conversation_ids(
    items: list[BrowserConversationSummary],
) -> None:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.external_conversation_id] = (
            counts.get(item.external_conversation_id, 0) + 1
        )
    for item in items:
        if counts[item.external_conversation_id] <= 1:
            continue
        identity = "|".join(
            [
                item.recruiter_name,
                item.job_title or "",
                item.company_name or "",
            ]
        )
        item.external_conversation_id = (
            f"derived:{hashlib.sha256(identity.encode()).hexdigest()}"
        )


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
    return [
        item
        for item in window
        if _message_key(item) not in seen
        or (not _has_stable_message_key(item) and item.unread_count > 0)
    ]


def _message_key(item: BrowserConversationSummary) -> str:
    if item.last_message_id:
        return f"{item.external_conversation_id}:{item.last_message_id}"
    preview = " ".join((item.last_message_text or "").split())
    if not preview:
        return f"{item.external_conversation_id}:conversation"
    preview_hash = hashlib.sha256(preview.encode()).hexdigest()
    return f"{item.external_conversation_id}:preview:{preview_hash}"


def _has_stable_message_key(item: BrowserConversationSummary) -> bool:
    return bool(
        item.last_message_id
        or " ".join((item.last_message_text or "").split())
    )


def _attempted_message_keys(
    candidates: list[BrowserConversationSummary],
    discovered: list[DiscoveredConversation],
) -> list[str]:
    """本轮已尝试项均去重；稳定键等内容变化，不稳定键由上层定时释放。"""
    if len(candidates) != len(discovered):
        raise ValueError("消息候选项与读取结果数量不一致")
    return [_message_key(summary) for summary in candidates]


def _matches_partition(
    item: BrowserConversationSummary, partition: str
) -> bool:
    if partition == "ALL":
        return True
    if partition == "NEW_GREETING":
        return item.category == "NEW_GREETING"
    return item.unread_count > 0
