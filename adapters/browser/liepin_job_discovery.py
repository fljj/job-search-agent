import json
import time
from datetime import UTC, datetime
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from adapters.browser.job_discovery import (
    DiscoveredJob,
    JobDiscoveryBatch,
    JobPrefilterState,
    classify_job_title,
    is_job_list_exhausted,
    next_job_search,
    select_job_candidates,
    verify_job_target,
)
from adapters.browser.playwright_reader import RawCdpPageReader, validate_local_cdp_url
from packages.browser_worker.config import BrowserSelectorsConfig
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import (
    BrowserJobSummary,
    PageType,
    Platform,
    SessionStatus,
)


class LiepinJobDiscoveryAdapter:
    """只读扫描猎聘常驻首页，并逐个读取临时职位详情。"""

    def __init__(self, config: BrowserSelectorsConfig) -> None:
        self.selectors = config.platforms[Platform.LIEPIN.value]

    def scan(
        self,
        cdp_url: str,
        *,
        search_key: str = "HOME",
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
        interval_seconds: int = 180,
    ) -> JobDiscoveryBatch:
        del refresh_before_scan
        # L4 需保留详情到“聊一聊”回读结束；共享首页一次只能持有一个临时详情。
        limit = min(limit, 1)
        configured_searches = search_keys or []
        if configured_searches and search_key not in configured_searches:
            search_key = configured_searches[0]
        validate_local_cdp_url(cdp_url)
        target = self._find_home_target(cdp_url)
        with RawCdpPageReader(target) as page:
            if switch_search_before_scan and configured_searches:
                self._activate_search(page, search_key)
            listing = extract_current_page(
                page,
                Platform.LIEPIN,
                self.selectors,
                self.selectors.version,
            )
            if (
                listing.status is not SessionStatus.SESSION_READY
                or listing.page_type is not PageType.JOB_LIST
            ):
                reason = listing.reason_codes[0] if listing.reason_codes else "UNKNOWN"
                raise ValueError(f"猎聘首页职位列表不可用: {reason}")
            candidates = self._candidates(listing.jobs, seen_job_ids, target_job_ids, limit)
            if not candidates:
                self._scroll_home(page)
                for _ in range(10):
                    time.sleep(0.25)
                    refreshed = extract_current_page(
                        page,
                        Platform.LIEPIN,
                        self.selectors,
                        self.selectors.version,
                    )
                    if (
                        refreshed.status is not SessionStatus.SESSION_READY
                        or refreshed.page_type is not PageType.JOB_LIST
                    ):
                        continue
                    listing = refreshed
                    candidates = self._candidates(
                        listing.jobs, seen_job_ids, target_job_ids, limit
                    )
                    if candidates:
                        break

            items: list[DiscoveredJob] = []
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
                    continue
                items.append(self._open_detail(cdp_url, page, summary))

            self._scroll_home(page)
            transient_ids = {
                item.summary.external_job_id
                for item in items
                if any(
                    reason in {"JOB_DETAIL_OPEN_FAILED", "JOB_DETAIL_NOT_READY"}
                    for reason in item.reason_codes
                )
            }
            seen = list(
                dict.fromkeys(
                    [
                        *(seen_job_ids or []),
                        *(
                            item.external_job_id
                            for item in candidates
                            if item.external_job_id not in transient_ids
                        ),
                    ]
                )
            )[-2000:]
            current = datetime.now(UTC)
            next_search, _ = next_job_search(
                search_key, configured_searches, exhausted=is_job_list_exhausted(
                    len(candidates), listing.cursor, previous_cursor
                )
            )
            return JobDiscoveryBatch(
                platform=Platform.LIEPIN,
                search_key=search_key,
                scroll_position=scroll_position + len(candidates),
                next_cursor=listing.cursor,
                scanned_at=current,
                next_scan_at=datetime.fromtimestamp(
                    current.timestamp() + interval_seconds, UTC
                ),
                items=items,
                seen_job_ids=seen,
                exhausted=is_job_list_exhausted(
                    len(candidates), listing.cursor, previous_cursor
                ),
                next_search_key=next_search,
                refresh_before_next_scan=False,
            )

    @staticmethod
    def _activate_search(page: RawCdpPageReader, search_key: str) -> None:
        switched = False
        for _ in range(40):
            state = page._evaluate(
                "(() => {"
                f"const expected={json.dumps(search_key)}.trim().toLocaleLowerCase();"
                "const visible=element => element.getClientRects().length > 0 "
                "&& getComputedStyle(element).visibility !== 'hidden';"
                "const items=[...document.querySelectorAll('[data-nick=\"switch-item\"]')]"
                ".filter(visible);"
                "const matches=items.filter(item => "
                "(item.textContent||'').trim().toLocaleLowerCase()===expected);"
                "if(matches.length!==1)return {found:false,active:false};"
                "const target=matches[0];"
                "const active=String(target.className).includes('switch-title-item-active');"
                "if(!active)target.click();"
                "return {found:true,active};})()"
            )
            if isinstance(state, dict) and state.get("found"):
                if state.get("active"):
                    if switched:
                        time.sleep(0.5)
                    return
                switched = True
                time.sleep(0.25)
                continue
            time.sleep(0.25)
        raise ValueError(f"猎聘职位入口不可用: {search_key}")

    @staticmethod
    def _candidates(
        jobs: list[BrowserJobSummary],
        seen_job_ids: list[str] | None,
        target_job_ids: set[str] | None,
        limit: int,
    ) -> list[BrowserJobSummary]:
        eligible = [
            item
            for item in jobs
            if target_job_ids is None or item.external_job_id in target_job_ids
        ]
        return select_job_candidates(
            eligible,
            seen_job_ids or [],
            scroll_position=0,
            limit=limit,
        )

    def _find_home_target(self, cdp_url: str) -> str:
        matches: list[str] = []
        for _ in range(20):
            matches = self._matching_home_targets(cdp_url)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                break
            time.sleep(0.5)
        raise ValueError(
            "未找到唯一猎聘首页"
            if not matches
            else "检测到多个猎聘首页"
        )

    def _matching_home_targets(self, cdp_url: str) -> list[str]:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read())
        matches: list[str] = []
        for target in targets:
            websocket_url = target.get("webSocketDebuggerUrl")
            parsed = urlparse(str(target.get("url") or ""))
            if (
                target.get("type") != "page"
                or not websocket_url
                or parsed.hostname != "c.liepin.com"
                or parsed.path not in {"", "/"}
            ):
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
        keep_target = False
        last_reason_codes: list[str] = []
        verify_company = not _is_masked_headhunter_company(summary)
        try:
            for _ in range(100):
                with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
                    targets = json.loads(response.read())
                target = next(
                    (
                        item
                        for item in targets
                        if str(item.get("id")) == created_target_id
                        and item.get("webSocketDebuggerUrl")
                        and created_target_id not in before
                    ),
                    None,
                )
                if target is None:
                    time.sleep(0.1)
                    continue
                try:
                    with RawCdpPageReader(str(target["webSocketDebuggerUrl"])) as detail_page:
                        result = extract_current_page(
                            detail_page,
                            Platform.LIEPIN,
                            self.selectors,
                            self.selectors.version,
                            expected_company=(
                                summary.company_name if verify_company else None
                            ),
                            expected_job_title=summary.title,
                            fallback_company=(
                                None if verify_company else summary.company_name
                            ),
                        )
                except (OSError, TimeoutError, ValueError):
                    time.sleep(0.1)
                    continue
                if (
                    result.status is not SessionStatus.SESSION_READY
                    or result.page_type is not PageType.JOB
                ):
                    last_reason_codes = result.reason_codes
                    time.sleep(0.1)
                    continue
                reasons = verify_job_target(
                    summary,
                    result,
                    verify_company=verify_company,
                )
                keep_target = not reasons
                return DiscoveredJob(
                    summary=summary,
                    detail=result if not reasons else None,
                    detail_target_id=(created_target_id if not reasons else None),
                    detail_target_url=(href if not reasons else None),
                    reason_codes=reasons,
                )
            return DiscoveredJob(
                summary=summary,
                reason_codes=last_reason_codes or ["JOB_DETAIL_NOT_READY"],
            )
        finally:
            if (
                not keep_target
                and created_target_id
                and created_target_id not in before
            ):
                self._close_target(cdp_url, created_target_id, href)

    def _scroll_home(self, page: RawCdpPageReader) -> None:
        page._evaluate(
            "(() => { const root = document.querySelector("
            f"{json.dumps(self.selectors.job_list_root)});"
            "if (root && root.scrollHeight > root.clientHeight) {"
            "root.scrollTop = root.scrollHeight;"
            "root.dispatchEvent(new Event('scroll', {bubbles: true})); return true; }"
            "window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'});"
            "return true; })()"
        )

    @staticmethod
    def _close_target(cdp_url: str, target_id: str, expected_url: str) -> None:
        """只关闭仍指向本次猎聘详情的 Worker 自有目标。"""
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
                or not current.path.startswith(("/job/", "/a/"))
                or not current.path.endswith(".shtml")
            ):
                return
            with urlopen(
                f"{cdp_url.rstrip('/')}/json/close/{target_id}", timeout=3
            ):
                pass
        except (OSError, TimeoutError, ValueError):
            return

    def close_details(self, cdp_url: str, batch: JobDiscoveryBatch) -> None:
        """职位沟通决策和已授权动作完成后关闭本批次创建的详情页。"""

        for item in batch.items:
            if item.detail_target_id and item.detail_target_url:
                self._close_target(
                    cdp_url,
                    item.detail_target_id,
                    item.detail_target_url,
                )


def _is_masked_headhunter_company(summary: BrowserJobSummary) -> bool:
    """猎聘 /a/ 猎头职位的列表公司可能是脱敏描述，不能与详情实名强比较。"""

    path = urlparse(summary.detail_url or "").path
    company = "".join(summary.company_name.split())
    return path.startswith("/a/") and company.startswith("某")
