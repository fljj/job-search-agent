from urllib.parse import urlparse

from playwright.sync_api import Browser, Locator, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from packages.browser_worker.config import BrowserSelectorsConfig
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import Platform, ReadResult
from packages.browser_worker.ports import ElementReader, PageReader


def validate_local_cdp_url(cdp_url: str) -> None:
    parsed = urlparse(cdp_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("CDP 地址必须使用本机 HTTP(S) 端点")
    if parsed.username or parsed.password:
        raise ValueError("CDP 地址不得包含凭证")


class PlaywrightElementReader(ElementReader):
    def __init__(self, source: Locator) -> None:
        self.source = source

    def text(self, selector: str) -> str | None:
        locator = self.source.locator(selector).first if selector else self.source
        value = locator.text_content(timeout=1000) if locator.count() else None
        return value.strip() if value else None

    def attribute(self, selector: str, name: str) -> str | None:
        locator = self.source.locator(selector).first if selector else self.source
        return locator.get_attribute(name, timeout=1000) if locator.count() else None


class PlaywrightPageReader(PlaywrightElementReader, PageReader):
    def __init__(self, page: Page) -> None:
        self.page = page
        super().__init__(page.locator("html"))

    @property
    def url(self) -> str:
        return self.page.url

    @property
    def title(self) -> str:
        return self.page.title()

    def text(self, selector: str) -> str | None:
        locator = self.page.locator(selector).first
        value = locator.text_content(timeout=1000) if locator.count() else None
        return value.strip() if value else None

    def attribute(self, selector: str, name: str) -> str | None:
        locator = self.page.locator(selector).first
        return locator.get_attribute(name, timeout=1000) if locator.count() else None

    def exists(self, selector: str) -> bool:
        locator = self.page.locator(selector)
        return any(locator.nth(index).is_visible(timeout=500) for index in range(locator.count()))

    def elements(self, selector: str) -> list[ElementReader]:
        locator = self.page.locator(selector)
        return [PlaywrightElementReader(locator.nth(index)) for index in range(locator.count())]


class PlaywrightReadOnlyAdapter:
    def __init__(self, platform: Platform, config: BrowserSelectorsConfig) -> None:
        self.platform = platform
        self.config = config

    def read_current_page(
        self, cdp_url: str, expected_company: str | None = None,
        expected_job_title: str | None = None, expected_recruiter: str | None = None,
    ) -> ReadResult:
        validate_local_cdp_url(cdp_url)
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            page = _current_page(browser)
            return extract_current_page(PlaywrightPageReader(page), self.platform,
                                        self.config.platforms[self.platform.value],
                                        self.config.version, expected_company,
                                        expected_job_title, expected_recruiter)


class BossReadOnlyAdapter(PlaywrightReadOnlyAdapter):
    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.BOSS, config)


class MaimaiReadOnlyAdapter(PlaywrightReadOnlyAdapter):
    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.MAIMAI, config)


def _current_page(browser: Browser) -> Page:
    pages = [page for context in browser.contexts for page in context.pages]
    if not pages:
        raise ValueError("未找到可读取的浏览器页面")
    for page in reversed(pages):
        try:
            if page.evaluate("document.hasFocus()"):
                return page
        except PlaywrightError:
            continue
    return pages[-1]
