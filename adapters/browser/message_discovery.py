import hashlib
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse
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


class DiscoveredConversation(BaseModel):
    summary: BrowserConversationSummary
    detail: ReadResult | None = None
    job_detail: ReadResult | None = None
    job_source_url: str | None = None
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
        priority_conversation_ids: list[str] | None = None,
        excluded_conversation_ids: list[str] | None = None,
        terminal_message_ids: dict[str, str] | None = None,
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
            if scroll_position == 0:
                self._restore_latest_messages(page)
            listing = extract_conversation_list(
                page, self.platform, self.selectors, self.selectors.version
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
                and not (
                    item.external_conversation_id in (terminal_message_ids or {})
                    and item.last_message_id
                    == (terminal_message_ids or {}).get(item.external_conversation_id)
                )
            ]
            candidates = select_discovery_candidates(
                eligible,
                stable_seen_message_keys,
                priority_conversation_ids=priority_conversation_ids,
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
            next_position = min(len(eligible), scroll_position + limit)
            updated_seen = [
                *stable_seen_message_keys,
                *_attempted_message_keys(candidates, discovered),
            ]
            exhausted = next_position >= len(eligible) and not listing.cursor
            if exhausted:
                self._restore_latest_messages(page)
            elif next_position >= len(eligible):
                self._scroll_list(page, to_end=True)
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

    def _restore_latest_messages(self, page: RawCdpPageReader) -> None:
        for _ in range(8):
            self._scroll_list(page, to_end=False)
            time.sleep(0.5)
            position = page._evaluate(
                "(() => { const element = document.querySelector("
                f"{json.dumps(self.selectors.conversation_list_root)}); "
                "return element?.scrollTop ?? null; })()"
            )
            if position is None or float(position) <= 1:
                return

    def _scroll_list(self, page: RawCdpPageReader, *, to_end: bool) -> None:
        target = "element.scrollHeight" if to_end else "0"
        page._evaluate(
            "(() => { const element = document.querySelector("
            f"{json.dumps(self.selectors.conversation_list_root)}); "
            f"if (!element) return false; element.scrollTop = {target}; "
            "element.dispatchEvent(new Event('scroll', {bubbles: true})); return true; })()"
        )

    def _find_list_target(self, cdp_url: str) -> str:
        matches = self._matching_list_targets(cdp_url)
        if len(matches) != 1:
            raise ValueError(
                f"未找到唯一 {self.platform.value} 消息列表页"
                if not matches
                else f"检测到多个 {self.platform.value} 消息列表页"
            )
        return matches[0]

    def _matching_list_targets(self, cdp_url: str) -> list[str]:
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
        return matches

    def _include_summary(self, _summary: BrowserConversationSummary) -> bool:
        return True

    def _detail_exclusion_reason(self, _detail: ReadResult) -> str | None:
        return None

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
            "try { return String(JSON.parse(decodeURIComponent(raw))[jsonKey]) === expected; } "
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
            self._prepare_conversation_detail(page)
            detail = extract_current_page(
                page,
                self.platform,
                self.selectors,
                self.selectors.version,
                expected_recruiter=summary.recruiter_name,
            )
            if (
                detail.page_type is PageType.CONVERSATION
                and detail.conversation
                and detail.conversation.messages
            ):
                reasons = _verify_target(summary, detail)
                if not reasons:
                    exclusion_reason = self._detail_exclusion_reason(detail)
                    if exclusion_reason:
                        return DiscoveredConversation(
                            summary=summary,
                            reason_codes=[exclusion_reason],
                        )
                if not reasons and summary.external_conversation_id.startswith(
                    "derived:"
                ):
                    detail.conversation.external_conversation_id = (
                        summary.external_conversation_id
                    )
                linked_href = self._linked_job_href(page) if not reasons else None
                linked_source_url = (
                    linked_href
                    if (urlparse(linked_href).hostname or "").lower()
                    in self.selectors.allowed_hosts
                    else None
                )
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
                    job_source_url=linked_source_url,
                    reason_codes=reasons,
                )
            time.sleep(0.1)
        return DiscoveredConversation(
            summary=summary,
            reason_codes=["CONVERSATION_DETAIL_NOT_READY"],
        )

    def _prepare_conversation_detail(self, _page: RawCdpPageReader) -> None:
        """允许平台适配器在读取前补充只读标准化属性。"""

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
                            self.selectors.version,
                        )
                        if (
                            result.status is SessionStatus.SESSION_READY
                            and result.page_type is PageType.JOB
                            and result.job is not None
                        ):
                            if created_target_id and target_id == created_target_id:
                                self._close_target(cdp_url, target_id, href)
                            if cache is not None:
                                cache[href] = result
                            return result
                except (OSError, TimeoutError, ValueError):
                    continue
            time.sleep(0.1)
        for target_id in opened_target_ids:
            if not target_id:
                continue
            if created_target_id and target_id == created_target_id:
                self._close_target(cdp_url, target_id, href)
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
    def _close_target(cdp_url: str, target_id: str, expected_url: str) -> None:
        """只关闭仍指向本次关联职位的 Worker 自有目标。"""
        try:
            with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                targets = json.loads(response.read())
            target = next(
                (item for item in targets if str(item.get("id")) == target_id),
                None,
            )
            current = urlparse(str(target.get("url") or "")) if target else None
            expected = urlparse(expected_url)
            if (
                target is None
                or target.get("type") != "page"
                or current is None
                or current.hostname != expected.hostname
                or current.path != expected.path
                or not _is_supported_job_detail_path(current.path)
            ):
                return
            with urlopen(
                f"{cdp_url.rstrip('/')}/json/close/{target_id}", timeout=3
            ):
                pass
        except (OSError, TimeoutError, ValueError):
            return


class BossMessageDiscoveryAdapter(MessageDiscoveryAdapter):
    list_page_url = "https://www.zhipin.com/web/geek/chat"

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.BOSS, config)

    def ensure_list_page(self, cdp_url: str) -> bool:
        """消息页缺失时重新创建；返回是否执行了恢复。"""
        validate_local_cdp_url(cdp_url)
        matches = self._matching_list_targets(cdp_url)
        if len(matches) == 1:
            return False
        if len(matches) > 1:
            raise ValueError("检测到多个 BOSS 消息列表页，禁止继续自动创建")
        request = Request(
            f"{cdp_url.rstrip('/')}/json/new?"
            f"{quote(self.list_page_url, safe=':/?=&%')}",
            method="PUT",
        )
        with urlopen(request, timeout=3):
            return True


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
    parsed = urlparse(href)
    query_job_id = parse_qs(parsed.query).get("job_id", [None])[0]
    if query_job_id:
        return query_job_id
    boss_match = re.search(r"/job_detail/([^/.]+)\.html", parsed.path)
    if boss_match:
        return boss_match.group(1)
    liepin_match = re.search(r"/(?:job|a)/([^/.]+)\.shtml", parsed.path)
    return liepin_match.group(1) if liepin_match else None


def _is_supported_job_detail_path(path: str) -> bool:
    return bool(
        re.search(r"/job_detail/[^/]+\.html$", path)
        or re.search(r"/(?:job|a)/[^/]+\.shtml$", path)
    )


def _normalize_duplicate_conversation_ids(
    items: list[BrowserConversationSummary],
) -> None:
    groups: dict[str, list[BrowserConversationSummary]] = {}
    for item in items:
        groups.setdefault(item.external_conversation_id, []).append(item)
    for group in groups.values():
        if len(group) <= 1:
            continue
        job_ids = [item.external_job_id for item in group]
        jobs_are_unique = (
            all(job_ids)
            and len(set(job_ids)) == len(job_ids)
        )
        composite_identities = [
            "|".join(
                [
                    item.recruiter_name,
                    item.job_title or "",
                    item.company_name or "",
                ]
            )
            for item in group
        ]
        # BOSS 当前页面可能把同名招聘人的多个列表项暴露为同一个 d-c，且不提供
        # data-job-id。此时列表中的招聘人、公司和职位摘要仍可唯一定位条目；只有
        # 复合摘要全部唯一且包含额外身份信息时，才允许把派生身份用于写操作。
        composites_are_reliable = (
            len(set(composite_identities)) == len(group)
            and all(item.job_title or item.company_name for item in group)
        )
        for item, identity in zip(group, composite_identities, strict=True):
            item.external_conversation_id = (
                f"derived:{hashlib.sha256(identity.encode()).hexdigest()}"
            )
            if not jobs_are_unique and not composites_are_reliable:
                item.identity_reliable = False


def select_discovery_candidates(
    items: list[BrowserConversationSummary],
    seen_message_keys: list[str],
    *,
    priority_conversation_ids: list[str] | None = None,
    scroll_position: int,
    limit: int,
) -> list[BrowserConversationSummary]:
    """列表重排后仍按最后消息去重，同时保留虚拟滚动位置。"""
    seen = set(seen_message_keys)
    priority = set(priority_conversation_ids or [])
    window = items[scroll_position : scroll_position + limit]
    return [
        item
        for item in window
        if item.external_conversation_id in priority
        or _message_key(item) not in seen
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
