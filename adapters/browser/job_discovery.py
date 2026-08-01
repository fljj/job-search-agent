import json
import re
import time
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from adapters.browser.playwright_reader import RawCdpPageReader, validate_local_cdp_url
from packages.browser_worker.config import BrowserSelectorsConfig
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import (
    BrowserJobSummary,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


class DiscoveredJob(BaseModel):
    summary: BrowserJobSummary
    detail: ReadResult | None = None
    detail_target_id: str | None = None
    detail_target_url: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class JobDiscoveryBatch(BaseModel):
    platform: Platform
    search_key: str
    scroll_position: int = Field(ge=0)
    next_cursor: str | None = None
    scanned_at: datetime
    next_scan_at: datetime
    items: list[DiscoveredJob] = Field(default_factory=list)
    seen_job_ids: list[str] = Field(default_factory=list, max_length=2000)
    exhausted: bool = False
    next_search_key: str | None = None
    refresh_before_next_scan: bool = False


class JobPrefilterState(StrEnum):
    RELEVANT = "RELEVANT"
    IRRELEVANT = "IRRELEVANT"
    UNKNOWN = "UNKNOWN"


class BossJobDiscoveryAdapter:
    """读取搜索列表并打开职位详情；不执行沟通或评分决策。"""

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        self.config = config
        self.selectors = config.platforms[Platform.BOSS.value]

    def scan(
        self,
        cdp_url: str,
        *,
        search_key: str = "CURRENT_SEARCH",
        search_keys: list[str] | None = None,
        refresh_before_scan: bool = False,
        switch_search_before_scan: bool = False,
        scroll_position: int = 0,
        previous_cursor: str | None = None,
        seen_job_ids: list[str] | None = None,
        target_job_ids: set[str] | None = None,
        irrelevant_title_keywords: list[str] | None = None,
        relevant_title_keywords: list[str] | None = None,
        direction_title_keywords: list[str] | None = None,
        limit: int = 20,
        interval_seconds: int = 30,
    ) -> JobDiscoveryBatch:
        validate_local_cdp_url(cdp_url)
        target = self._find_list_target(cdp_url)
        with RawCdpPageReader(target) as page:
            # BOSS 消息和职位会通过站内事件自行更新。主动刷新容易使长时间运行的
            # 登录会话失效；该参数仅为兼容历史游标保留，不得驱动页面刷新。
            if (
                switch_search_before_scan
                and search_keys
                and search_key in search_keys
            ):
                self._activate_search(page, search_key)
            listing = None
            for _ in range(10):
                listing = extract_current_page(
                    page, Platform.BOSS, self.selectors, self.config.version
                )
                if (
                    listing.status is SessionStatus.SESSION_READY
                    and listing.page_type is PageType.JOB_LIST
                ):
                    break
                time.sleep(0.5)
            assert listing is not None
            if (
                listing.status is not SessionStatus.SESSION_READY
                or listing.page_type is not PageType.JOB_LIST
            ):
                raise ValueError("BOSS 职位列表结构不可用")
            candidates = select_job_candidates(
                [
                    item
                    for item in listing.jobs
                    if target_job_ids is None
                    or item.external_job_id in target_job_ids
                ],
                seen_job_ids or [],
                scroll_position=0,
                limit=limit,
            )
            if not candidates:
                page._evaluate(
                    "(() => { const element = document.querySelector("
                    f"{json.dumps(self.selectors.job_list_root)}); "
                    "if (!element) return false; element.scrollTop = element.scrollHeight; "
                    "element.dispatchEvent(new Event('scroll', {bubbles: true})); return true; })()"
                )
                # BOSS 使用虚拟列表。当前视窗没有新职位时，等待滚动加载并重新
                # 提取；不能在触发 scroll 后立刻把搜索入口判定为已遍历完成。
                for _ in range(10):
                    time.sleep(0.25)
                    refreshed = extract_current_page(
                        page, Platform.BOSS, self.selectors, self.config.version
                    )
                    if (
                        refreshed.status is not SessionStatus.SESSION_READY
                        or refreshed.page_type is not PageType.JOB_LIST
                    ):
                        continue
                    listing = refreshed
                    candidates = select_job_candidates(
                        [
                            item
                            for item in listing.jobs
                            if target_job_ids is None
                            or item.external_job_id in target_job_ids
                        ],
                        seen_job_ids or [],
                        scroll_position=0,
                        limit=limit,
                    )
                    if candidates:
                        break
            items = []
            for summary in candidates:
                prefilter = classify_job_title(
                    summary.title,
                    direction_keywords=direction_title_keywords or [],
                    irrelevant_keywords=irrelevant_title_keywords or [],
                    relevant_keywords=relevant_title_keywords or [],
                )
                if prefilter is JobPrefilterState.IRRELEVANT:
                    items.append(
                        DiscoveredJob(
                            summary=summary,
                            reason_codes=["TITLE_STRONGLY_IRRELEVANT"],
                        )
                    )
                else:
                    opened = self._open_detail(cdp_url, page, summary)
                    if opened.detail_target_id and opened.detail_target_url:
                        self._close_target(
                            cdp_url,
                            opened.detail_target_id,
                            opened.detail_target_url,
                        )
                        opened.detail_target_id = None
                        opened.detail_target_url = None
                    items.append(opened)
            page._evaluate(
                "(() => { const element = document.querySelector("
                f"{json.dumps(self.selectors.job_list_root)}); "
                "if (!element) return false; element.scrollTop = element.scrollHeight; "
                "element.dispatchEvent(new Event('scroll', {bubbles: true})); return true; })()"
            )
            next_position = scroll_position + len(candidates)
            exhausted = is_job_list_exhausted(
                len(candidates), listing.cursor, previous_cursor
            )
            transient_ids = {
                item.summary.external_job_id
                for item in items
                if any(
                    reason in {"JOB_DETAIL_OPEN_FAILED", "JOB_DETAIL_NOT_READY"}
                    for reason in item.reason_codes
                )
            }
            seen = list(dict.fromkeys([
                *(seen_job_ids or []),
                *(
                    item.external_job_id
                    for item in candidates
                    if item.external_job_id not in transient_ids
                ),
            ]))[-2000:]
            current = datetime.now(UTC)
            next_search, _ = next_job_search(
                search_key, search_keys or [], exhausted=exhausted
            )
            return JobDiscoveryBatch(
                platform=Platform.BOSS,
                search_key=search_key,
                scroll_position=next_position,
                next_cursor=listing.cursor,
                scanned_at=current,
                next_scan_at=datetime.fromtimestamp(
                    current.timestamp() + interval_seconds, UTC
                ),
                items=items,
                seen_job_ids=seen,
                exhausted=exhausted,
                next_search_key=next_search,
                refresh_before_next_scan=False,
            )

    def _activate_search(
        self, page: RawCdpPageReader, search_key: str
    ) -> None:
        for _ in range(40):
            activated = page._evaluate(
                "(() => {"
                f"const expected = {json.dumps(search_key)}.trim().toLocaleLowerCase();"
                "const visible = element => element.getClientRects().length > 0 "
                "&& getComputedStyle(element).visibility !== 'hidden';"
                "const candidates = Array.from(document.querySelectorAll("
                "'.c-expect-select a'));"
                "const target = candidates.find(element => visible(element) "
                "&& (() => {"
                "const actual = (element.textContent || '').trim().toLocaleLowerCase();"
                "return actual === expected || actual.startsWith(`${expected}(`);"
                "})());"
                "if (!target) return false;"
                "target.click(); return true;"
                "})()"
            )
            if activated:
                return
            time.sleep(0.25)
        raise ValueError(f"BOSS 职位搜索入口不可用: {search_key}")

    def _find_list_target(self, cdp_url: str) -> str:
        matches: list[str] = []
        for _ in range(60):
            matches = self._matching_list_targets(cdp_url)
            if len(matches) == 1:
                return matches[0]
            time.sleep(0.5)
        raise ValueError(
            "未找到唯一 BOSS 职位列表页"
            if not matches
            else "检测到多个 BOSS 职位列表页"
        )

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
                    if page.exists(self.selectors.job_list_root):
                        matches.append(str(websocket_url))
            except (OSError, TimeoutError, ValueError):
                continue
        return matches

    def _open_detail(
        self,
        cdp_url: str,
        page: RawCdpPageReader,
        summary: BrowserJobSummary,
    ) -> DiscoveredJob:
        href = summary.detail_url
        if not href:
            return DiscoveredJob(
                summary=summary, reason_codes=["JOB_DETAIL_LINK_MISSING"]
            )
        href = urljoin(page.url, href)
        if urlparse(href).hostname not in self.selectors.allowed_hosts:
            return DiscoveredJob(
                summary=summary, reason_codes=["JOB_DETAIL_LINK_INVALID"]
            )
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
            return DiscoveredJob(
                summary=summary, reason_codes=["JOB_DETAIL_OPEN_FAILED"]
            )
        created_target_id = str(created_target.get("id") or "")
        for _ in range(100):
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
                try:
                    with RawCdpPageReader(str(target["webSocketDebuggerUrl"])) as detail_page:
                        result = extract_current_page(
                            detail_page,
                            Platform.BOSS,
                            self.selectors,
                            self.config.version,
                            expected_company=summary.company_name,
                            expected_job_title=summary.title,
                        )
                    if (
                        result.status is not SessionStatus.SESSION_READY
                        or result.page_type is not PageType.JOB
                    ):
                        continue
                    owns_target = bool(
                        created_target_id
                        and target_id == created_target_id
                        and target_id not in before
                    )
                    reasons = verify_job_target(summary, result)
                    if reasons and owns_target:
                        self._close_target(cdp_url, target_id, href)
                    return DiscoveredJob(
                        summary=summary,
                        detail=result if not reasons else None,
                        detail_target_id=(
                            target_id if not reasons and owns_target else None
                        ),
                        detail_target_url=(
                            href if not reasons and owns_target else None
                        ),
                        reason_codes=reasons,
                    )
                except (OSError, TimeoutError, ValueError):
                    continue
            time.sleep(0.1)
        if created_target_id and created_target_id not in before:
            self._close_target(cdp_url, created_target_id, href)
        return DiscoveredJob(
            summary=summary, reason_codes=["JOB_DETAIL_NOT_READY"]
        )

    @staticmethod
    def _close_target(cdp_url: str, target_id: str, expected_url: str) -> None:
        """只关闭仍指向本次职位详情的 Worker 自有目标。"""
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
                or "/job_detail/" not in current.path
            ):
                return
            with urlopen(
                f"{cdp_url.rstrip('/')}/json/close/{target_id}", timeout=3
            ):
                pass
        except (OSError, TimeoutError, ValueError):
            return

    def close_details(self, cdp_url: str, batch: JobDiscoveryBatch) -> None:
        """批次完成后关闭扫描器创建的详情页，不影响用户原有标签页。"""
        for item in batch.items:
            if item.detail_target_id and item.detail_target_url:
                self._close_target(
                    cdp_url,
                    item.detail_target_id,
                    item.detail_target_url,
                )


def select_job_candidates(
    items: list[BrowserJobSummary],
    seen_job_ids: list[str],
    *,
    scroll_position: int,
    limit: int,
) -> list[BrowserJobSummary]:
    seen = set(seen_job_ids)
    return [
        item
        for item in items[scroll_position:]
        if item.external_job_id not in seen
    ][:limit]


def is_obviously_irrelevant_title(
    title: str,
    irrelevant_keywords: list[str],
    relevant_keywords: list[str] | None = None,
) -> bool:
    normalized_title = "".join(title.casefold().split())
    if any(
        normalized_keyword in normalized_title
        for keyword in (relevant_keywords or [])
        if (normalized_keyword := "".join(keyword.casefold().split()))
    ):
        return False
    return any(
        (
            normalized_title == normalized_keyword
            if normalized_keyword.isascii() and len(normalized_keyword) <= 3
            else normalized_keyword in normalized_title
        )
        for keyword in irrelevant_keywords
        if (normalized_keyword := "".join(keyword.casefold().split()))
    )


def is_potentially_relevant_title(
    title: str,
    direction_keywords: list[str],
) -> bool:
    normalized_title = "".join(title.casefold().split())
    return any(
        (
            re.search(
                rf"(?<![a-z0-9]){re.escape(keyword.casefold().strip())}"
                r"(?![a-z0-9])",
                title.casefold(),
            )
            is not None
            if normalized_keyword.isascii() and len(normalized_keyword) <= 3
            else normalized_keyword in normalized_title
        )
        for keyword in direction_keywords
        if (normalized_keyword := "".join(keyword.casefold().split()))
    )


def classify_job_title(
    title: str,
    *,
    direction_keywords: list[str],
    irrelevant_keywords: list[str],
    relevant_keywords: list[str],
) -> JobPrefilterState:
    """标题只做强证据预筛；未命中正向词不等于无关。"""
    if is_obviously_irrelevant_title(title, irrelevant_keywords, relevant_keywords):
        return JobPrefilterState.IRRELEVANT
    if is_potentially_relevant_title(title, direction_keywords):
        return JobPrefilterState.RELEVANT
    return JobPrefilterState.UNKNOWN


def next_job_search(
    current: str, search_keys: list[str], *, exhausted: bool
) -> tuple[str, bool]:
    """返回下一次扫描入口；第二项为兼容旧游标保留且始终禁止刷新。"""
    if not exhausted or not search_keys:
        return current, False
    try:
        current_index = search_keys.index(current)
    except ValueError:
        return search_keys[0], False
    next_index = (current_index + 1) % len(search_keys)
    return search_keys[next_index], False


def is_job_list_exhausted(
    candidate_count: int,
    current_cursor: str | None,
    previous_cursor: str | None,
) -> bool:
    return candidate_count == 0 and (
        not current_cursor or current_cursor == previous_cursor
    )


def verify_job_target(
    summary: BrowserJobSummary, detail: ReadResult
) -> list[str]:
    job = detail.job
    if detail.page_type is not PageType.JOB or job is None:
        return ["JOB_DETAIL_MISSING"]
    if job.external_job_id and job.external_job_id != summary.external_job_id:
        return ["JOB_ID_MISMATCH"]
    if not (
        summary.company_name in job.company_name
        or job.company_name in summary.company_name
    ):
        return ["JOB_COMPANY_MISMATCH"]
    if job.title != summary.title:
        return ["JOB_TITLE_MISMATCH"]
    return []
