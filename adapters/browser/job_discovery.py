import json
import time
from datetime import UTC, datetime
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
        scroll_position: int = 0,
        seen_job_ids: list[str] | None = None,
        limit: int = 20,
        interval_seconds: int = 30,
    ) -> JobDiscoveryBatch:
        validate_local_cdp_url(cdp_url)
        target = self._find_list_target(cdp_url)
        with RawCdpPageReader(target) as page:
            listing = extract_current_page(
                page, Platform.BOSS, self.selectors, self.config.version
            )
            if (
                listing.status is not SessionStatus.SESSION_READY
                or listing.page_type is not PageType.JOB_LIST
            ):
                raise ValueError("BOSS 职位列表结构不可用")
            candidates = select_job_candidates(
                listing.jobs,
                seen_job_ids or [],
                scroll_position=scroll_position,
                limit=limit,
            )
            items = [
                self._open_detail(cdp_url, page, summary)
                for summary in candidates
            ]
            page._evaluate(
                "(() => { const element = document.querySelector("
                f"{json.dumps(self.selectors.job_list_root)}); "
                "if (!element) return false; element.scrollTop = element.scrollHeight; "
                "element.dispatchEvent(new Event('scroll', {bubbles: true})); return true; })()"
            )
            next_position = min(
                len(listing.jobs), scroll_position + limit
            )
            exhausted = next_position >= len(listing.jobs) and not listing.cursor
            seen = list(dict.fromkeys([
                *(seen_job_ids or []),
                *(item.external_job_id for item in candidates),
            ]))[-2000:]
            current = datetime.now(UTC)
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
                    if page.exists(self.selectors.job_list_root):
                        matches.append(str(websocket_url))
            except (OSError, TimeoutError, ValueError):
                continue
        if len(matches) != 1:
            raise ValueError(
                "未找到唯一 BOSS 职位列表页"
                if not matches
                else "检测到多个 BOSS 职位列表页"
            )
        return matches[0]

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
                    self._close_target(cdp_url, target_id)
                    reasons = verify_job_target(summary, result)
                    return DiscoveredJob(
                        summary=summary,
                        detail=result if not reasons else None,
                        reason_codes=reasons,
                    )
                except (OSError, TimeoutError, ValueError):
                    continue
            time.sleep(0.1)
        if created_target_id:
            self._close_target(cdp_url, created_target_id)
        return DiscoveredJob(
            summary=summary, reason_codes=["JOB_DETAIL_NOT_READY"]
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
        for item in items[scroll_position : scroll_position + limit]
        if item.external_job_id not in seen
    ]


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
