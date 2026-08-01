import json
from typing import Any, cast
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import Browser, Locator, Page
from playwright.sync_api import Error as PlaywrightError
from websockets.sync.client import ClientConnection, connect

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

    def value(self, selector: str) -> str | None:
        locator = self.page.locator(selector).first
        return locator.input_value(timeout=1000) if locator.count() else None

    def elements(self, selector: str) -> list[ElementReader]:
        locator = self.page.locator(selector)
        return [PlaywrightElementReader(locator.nth(index)) for index in range(locator.count())]


class RawCdpElementReader(ElementReader):
    def __init__(self, page: "RawCdpPageReader", root_selector: str, index: int) -> None:
        self.page = page
        self.root_selector = root_selector
        self.index = index

    def text(self, selector: str) -> str | None:
        return self.page._element_value(self.root_selector, self.index, selector, "textContent")

    def attribute(self, selector: str, name: str) -> str | None:
        return self.page._element_value(self.root_selector, self.index, selector, "attribute", name)


class RawCdpPageReader(PageReader):
    """通过原生 CDP 只读查询 DOM，断开时不会接管或关闭浏览器标签页。"""

    def __init__(self, websocket_url: str) -> None:
        self.connection: ClientConnection = connect(websocket_url, open_timeout=3)
        self.message_id = 0

    def __enter__(self) -> "RawCdpPageReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    @property
    def url(self) -> str:
        return str(self._evaluate("location.href"))

    @property
    def title(self) -> str:
        return str(self._evaluate("document.title"))

    def text(self, selector: str) -> str | None:
        expression = (
            "(() => { const element = document.querySelector("
            f"{json.dumps(selector)}); return element?.textContent?.trim() || null; }})()"
        )
        value = self._evaluate(expression)
        return str(value) if value is not None else None

    def attribute(self, selector: str, name: str) -> str | None:
        expression = (
            "(() => { const element = document.querySelector("
            f"{json.dumps(selector)}); return element?.getAttribute({json.dumps(name)}) || null; }})()"
        )
        value = self._evaluate(expression)
        return str(value) if value is not None else None

    def exists(self, selector: str) -> bool:
        expression = (
            "(() => Array.from(document.querySelectorAll("
            f"{json.dumps(selector)})).some(element => "
            "element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden'))()"
        )
        return bool(self._evaluate(expression))

    def value(self, selector: str) -> str | None:
        expression = (
            "(() => { const element = document.querySelector("
            f"{json.dumps(selector)}); return element?.value || null; }})()"
        )
        value = self._evaluate(expression)
        return str(value) if value is not None else None

    def elements(self, selector: str) -> list[ElementReader]:
        count = int(self._evaluate(
            f"document.querySelectorAll({json.dumps(selector)}).length"
        ))
        return [RawCdpElementReader(self, selector, index) for index in range(count)]

    def _element_value(
        self, root_selector: str, index: int, selector: str, operation: str,
        attribute_name: str | None = None,
    ) -> str | None:
        child = (
            f"root.querySelector({json.dumps(selector)})"
            if selector
            else "root"
        )
        result = (
            "target?.textContent?.trim() || null"
            if operation == "textContent"
            else f"target?.getAttribute({json.dumps(attribute_name)}) || null"
        )
        expression = (
            "(() => { const root = document.querySelectorAll("
            f"{json.dumps(root_selector)})[{index}]; if (!root) return null; "
            f"const target = {child}; return {result}; }})()"
        )
        value = self._evaluate(expression)
        return str(value) if value is not None else None

    def _evaluate(self, expression: str) -> Any:
        result = self._command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        ).get("result", {})
        if result.get("subtype") == "error":
            raise ValueError("CDP 页面脚本执行失败")
        return result.get("value")

    def _command(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.message_id += 1
        self.connection.send(json.dumps({
            "id": self.message_id,
            "method": method,
            "params": params,
        }))
        while True:
            response = json.loads(self.connection.recv(timeout=3))
            if response.get("id") != self.message_id:
                continue
            if "error" in response:
                raise ValueError("CDP 页面读取失败")
            return cast(dict[str, Any], response.get("result", {}))


class PlaywrightReadOnlyAdapter:
    def __init__(self, platform: Platform, config: BrowserSelectorsConfig) -> None:
        self.platform = platform
        self.config = config

    def read_current_page(
        self, cdp_url: str, expected_company: str | None = None,
        expected_job_title: str | None = None, expected_recruiter: str | None = None,
    ) -> ReadResult:
        validate_local_cdp_url(cdp_url)
        selectors = self.config.platforms[self.platform.value]
        target = _current_cdp_target(
            cdp_url,
            selectors.allowed_hosts,
            unique_home_host=(
                "c.liepin.com" if self.platform is Platform.LIEPIN else None
            ),
        )
        with RawCdpPageReader(target["webSocketDebuggerUrl"]) as page:
            return extract_current_page(
                page,
                self.platform,
                selectors,
                selectors.version,
                expected_company,
                expected_job_title,
                expected_recruiter,
            )


class BossReadOnlyAdapter(PlaywrightReadOnlyAdapter):
    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.BOSS, config)


class LiepinReadOnlyAdapter(PlaywrightReadOnlyAdapter):
    def __init__(self, config: BrowserSelectorsConfig) -> None:
        super().__init__(Platform.LIEPIN, config)


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


def _current_cdp_target(
    cdp_url: str,
    allowed_hosts: list[str],
    *,
    unique_home_host: str | None = None,
) -> dict[str, str]:
    with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
        targets = json.loads(response.read())
    pages = [
        target for target in targets
        if target.get("type") == "page"
        and target.get("webSocketDebuggerUrl")
        and urlparse(target.get("url", "")).hostname in allowed_hosts
    ]
    if not pages:
        raise ValueError("未找到当前平台页面，请在专用浏览器中打开目标页")
    if unique_home_host:
        home_pages = [
            target
            for target in pages
            if urlparse(target.get("url", "")).hostname == unique_home_host
            and urlparse(target.get("url", "")).path in {"", "/"}
        ]
        if len(home_pages) > 1:
            raise ValueError("找到多个猎聘首页，请只保留一个常驻首页")
    for target in pages:
        try:
            with RawCdpPageReader(target["webSocketDebuggerUrl"]) as page:
                if page._evaluate("document.hasFocus()"):
                    return cast(dict[str, str], target)
        except (OSError, TimeoutError, ValueError):
            continue
    return cast(dict[str, str], pages[0])
