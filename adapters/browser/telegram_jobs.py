import json
import re
import time
from datetime import UTC, datetime
from urllib.request import urlopen

from pydantic import BaseModel, Field

from adapters.browser.playwright_reader import RawCdpPageReader, validate_local_cdp_url
from apps.api.app.core.telegram_config import TelegramPolicyConfig
from packages.browser_worker.models import BrowserJob


class TelegramJobPost(BaseModel):
    channel_id: str
    channel_name: str
    message_id: str
    contact_username: str
    job: BrowserJob


class TelegramJobBatch(BaseModel):
    scanned_at: datetime
    posts: list[TelegramJobPost] = Field(default_factory=list)
    seen_post_ids: list[str] = Field(default_factory=list, max_length=2000)


class TelegramJobDiscoveryAdapter:
    """只读取白名单频道中的招聘帖子，不做评分或发送决策。"""

    def __init__(self, policy: TelegramPolicyConfig) -> None:
        self.policy = policy

    def scan(
        self,
        cdp_url: str,
        *,
        seen_post_ids: list[str] | None = None,
    ) -> TelegramJobBatch:
        validate_local_cdp_url(cdp_url)
        target = self._find_target(cdp_url)
        seen = set(seen_post_ids or [])
        posts: list[TelegramJobPost] = []
        with RawCdpPageReader(target) as page:
            self._clear_search(page)
            for channel in self.policy.channels:
                self._open_channel(page, channel.channel_id, channel.name)
                for _ in range(50):
                    current_hash = page._evaluate("location.hash")
                    message_count = page._evaluate(
                        "document.querySelectorAll('.Message[data-message-id]').length"
                    )
                    if (
                        current_hash == f"#{channel.channel_id}"
                        and channel.name in page.title
                        and isinstance(message_count, int)
                        and message_count > 0
                    ):
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError(f"Telegram 频道加载超时：{channel.name}")
                raw_posts = page._evaluate(
                    "[...document.querySelectorAll("
                    "'.Message[data-message-id]')].slice("
                    f"-{self.policy.scan_limit_per_channel}).map(element => ({{"
                    "message_id: element.dataset.messageId,"
                    "content: element.innerText || ''"
                    "}))"
                )
                if not isinstance(raw_posts, list):
                    continue
                for raw in raw_posts:
                    if not isinstance(raw, dict):
                        continue
                    message_id = str(raw.get("message_id") or "")
                    post_id = f"{channel.channel_id}:{message_id}"
                    if not message_id or post_id in seen:
                        continue
                    seen.add(post_id)
                    parsed = parse_telegram_job_posts(
                        channel.channel_id,
                        channel.name,
                        message_id,
                        str(raw.get("content") or ""),
                    )
                    posts.extend(parsed)
        return TelegramJobBatch(
            scanned_at=datetime.now(UTC),
            posts=posts,
            seen_post_ids=list(seen)[-2000:],
        )

    @staticmethod
    def _clear_search(page: RawCdpPageReader) -> None:
        page._evaluate(
            "(() => { const element = [...document.querySelectorAll("
            "\"input[placeholder='Search']\")].find(item => "
            "item.getClientRects().length > 0);"
            "if (!element || !element.value) return;"
            "const setter = Object.getOwnPropertyDescriptor("
            "HTMLInputElement.prototype, 'value').set;"
            "setter.call(element, '');"
            "element.dispatchEvent(new Event('input', {bubbles:true}));"
            "})()"
        )
        time.sleep(0.2)

    @staticmethod
    def _open_channel(
        page: RawCdpPageReader,
        channel_id: str,
        channel_name: str,
    ) -> None:
        point = page._evaluate(
            "(() => {"
            f"const expectedHash = '#{channel_id}';"
            "const link = [...document.querySelectorAll('a[href]')].find("
            "element => new URL(element.href).hash === expectedHash"
            ");"
            "if (!link) return null;"
            "link.scrollIntoView({block: 'center'});"
            "const rect = link.getBoundingClientRect();"
            "return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};"
            "})()"
        )
        if not isinstance(point, dict):
            page._evaluate(
                f"location.hash = {json.dumps(channel_id)}"
            )
            return
        coordinates = {
            "x": float(point["x"]),
            "y": float(point["y"]),
            "button": "left",
            "clickCount": 1,
        }
        page._command(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", **coordinates},
        )
        page._command(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", **coordinates},
        )

    @staticmethod
    def _find_target(cdp_url: str) -> str:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read())
        matches = [
            str(item["webSocketDebuggerUrl"])
            for item in targets
            if item.get("type") == "page"
            and str(item.get("url") or "").startswith("https://web.telegram.org/a/")
            and item.get("webSocketDebuggerUrl")
        ]
        if len(matches) != 1:
            raise ValueError(
                "未找到唯一 Telegram Web A 页面"
                if not matches
                else "检测到多个 Telegram Web A 页面"
            )
        return matches[0]


def parse_telegram_job_post(
    channel_id: str,
    channel_name: str,
    message_id: str,
    content: str,
) -> TelegramJobPost | None:
    posts = parse_telegram_job_posts(
        channel_id, channel_name, message_id, content
    )
    return posts[0] if posts else None


def parse_telegram_job_posts(
    channel_id: str,
    channel_name: str,
    message_id: str,
    content: str,
) -> list[TelegramJobPost]:
    if "#招聘" not in content:
        return []
    contact = _contact(content)
    title = _field(content, r"待招岗位[：:]\s*#?([^\n#]+)")
    company = _field(content, r"🏡\s*([^#\n]+)")
    if not contact:
        return []
    if not title or not company:
        return _parse_numbered_job_post(
            channel_id,
            channel_name,
            message_id,
            content,
            contact,
        )
    salary = _field(content, r"薪酬福利[：:]\s*([^\n]+)")
    cooperation = _field(content, r"合作方式[：:]\s*([^\n]+)") or ""
    work_mode = "REMOTE" if "远程" in cooperation else "UNKNOWN"
    location = "远程" if work_mode == "REMOTE" else None
    return [
        TelegramJobPost(
            channel_id=channel_id,
            channel_name=channel_name,
            message_id=message_id,
            contact_username=contact,
            job=BrowserJob(
                external_job_id=f"{channel_id}:{message_id}",
                title=title,
                company_name=company,
                industry="Web3",
                location=location,
                work_mode=work_mode,
                salary_text=salary,
                recruiter_name=contact,
                description=content,
                source_status="OPEN",
            ),
        )
    ]


def _parse_numbered_job_post(
    channel_id: str,
    channel_name: str,
    message_id: str,
    content: str,
    contact: str,
) -> list[TelegramJobPost]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    company = _aggregate_company(lines)
    numbered = [
        (int(match.group(1)), match.group(2).strip())
        for line in lines
        if (match := re.match(r"^(\d+)[.、]\s*(.+)$", line))
    ]
    if not company or len(numbered) < 2:
        return []
    remote = any("远程" in line for line in lines)
    global_context = "\n".join(
        line
        for line in lines
        if "远程" in line or line.startswith(("http://", "https://"))
    )
    posts: list[TelegramJobPost] = []
    for ordinal, (_, job_line) in enumerate(numbered, start=1):
        title = re.split(r"\s*(?:\(|（|[-–—]\s)", job_line, maxsplit=1)[0]
        title = title.strip(" #❗️")
        if not title:
            continue
        posts.append(
            TelegramJobPost(
                channel_id=channel_id,
                channel_name=channel_name,
                message_id=message_id,
                contact_username=contact,
                job=BrowserJob(
                    external_job_id=f"{channel_id}:{message_id}:{ordinal}",
                    title=title,
                    company_name=company,
                    industry="Web3",
                    location="远程" if remote else None,
                    work_mode="REMOTE" if remote else "UNKNOWN",
                    salary_text=None,
                    recruiter_name=contact,
                    description=(
                        f"{company}\n岗位：{job_line}\n{global_context}\n"
                        f"Telegram: {contact}"
                    ),
                    source_status="OPEN",
                ),
            )
        )
    return posts


def _aggregate_company(lines: list[str]) -> str | None:
    try:
        start = next(index for index, line in enumerate(lines) if "#招聘" in line)
    except StopIteration:
        return None
    for line in lines[start + 1 :]:
        if line.startswith(("#", "http://", "https://")):
            continue
        if re.match(r"^\d+[.、]", line):
            return None
        return re.split(r"\s+[-–—]\s+", line, maxsplit=1)[0].strip()
    return None


def _field(content: str, pattern: str) -> str | None:
    match = re.search(pattern, content, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _contact(content: str) -> str | None:
    match = re.search(
        r"Telegram\s*[：:]\s*(?:https?://)?(?:t\.me/)?@?([A-Za-z0-9_]{5,32})",
        content,
        re.IGNORECASE,
    )
    return f"@{match.group(1)}" if match else None
