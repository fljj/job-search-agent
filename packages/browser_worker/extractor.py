import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlparse

from packages.browser_worker.config import PlatformSelectors
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserConversationSummary,
    BrowserJob,
    BrowserJobSummary,
    BrowserMessage,
    BrowserPlatformConsent,
    MessageDirection,
    PageType,
    Platform,
    PlatformConsentType,
    ReadResult,
    SessionStatus,
)
from packages.browser_worker.ports import PageReader


def extract_current_page(
    page: PageReader,
    platform: Platform,
    selectors: PlatformSelectors,
    selector_version: str,
    expected_company: str | None = None,
    expected_job_title: str | None = None,
    expected_recruiter: str | None = None,
    fallback_company: str | None = None,
) -> ReadResult:
    host = (urlparse(page.url).hostname or "").lower()
    if host not in selectors.allowed_hosts:
        return _failure(
            page,
            platform,
            selector_version,
            SessionStatus.SESSION_TARGET_MISMATCH,
            "UNSUPPORTED_HOST",
        )
    if page.exists(selectors.verification_marker):
        return _failure(
            page,
            platform,
            selector_version,
            SessionStatus.SESSION_AUTH_REQUIRED,
            "VERIFICATION_REQUIRED",
        )
    if not page.exists(selectors.login_marker):
        return _failure(
            page, platform, selector_version, SessionStatus.SESSION_AUTH_REQUIRED, "LOGIN_REQUIRED"
        )
    if selectors.pending_user_input and (page.value(selectors.pending_user_input) or "").strip():
        return _failure(
            page,
            platform,
            selector_version,
            SessionStatus.SESSION_PAUSED,
            "PENDING_USER_INPUT",
        )
    if selectors.blocking_dialog_marker and page.exists(selectors.blocking_dialog_marker):
        return _failure(
            page,
            platform,
            selector_version,
            SessionStatus.SESSION_PAUSED,
            "BLOCKING_DIALOG_VISIBLE",
        )
    if page.exists(selectors.job_root):
        return _extract_job(
            page,
            platform,
            selectors,
            selector_version,
            expected_company,
            expected_job_title,
            fallback_company,
        )
    if page.exists(selectors.conversation_root):
        return _extract_conversation(
            page, platform, selectors, selector_version, expected_recruiter
        )
    if page.exists(selectors.job_list_root):
        return _extract_job_list(page, platform, selectors, selector_version)
    if page.exists(selectors.conversation_list_root):
        return _extract_conversation_list(page, platform, selectors, selector_version)
    return _failure(
        page,
        platform,
        selector_version,
        SessionStatus.SESSION_PAGE_CHANGED,
        "SUPPORTED_PAGE_ROOT_NOT_FOUND",
    )


def extract_conversation_list(
    page: PageReader,
    platform: Platform,
    selectors: PlatformSelectors,
    selector_version: str,
) -> ReadResult:
    """在对话详情与列表共存的页面中显式读取列表区域。"""
    if not page.exists(selectors.conversation_list_root):
        return _failure(
            page,
            platform,
            selector_version,
            SessionStatus.SESSION_PAGE_CHANGED,
            "CONVERSATION_LIST_NOT_FOUND",
        )
    return _extract_conversation_list(page, platform, selectors, selector_version)


def _extract_job_list(
    page: PageReader,
    platform: Platform,
    selectors: PlatformSelectors,
    version: str,
) -> ReadResult:
    jobs: list[BrowserJobSummary] = []
    diagnostics: list[str] = []
    elements = page.elements(selectors.job_list_items)
    for element in elements:
        detail_url = element.attribute(selectors.job_list_item_link, "href")
        external_id = element.attribute(
            "", selectors.job_list_item_id_attribute
        ) or _job_id_from_url(detail_url or "")
        title = element.text(selectors.job_list_item_title)
        company = _normalize_company_name(element.text(selectors.job_list_item_company))
        if not external_id:
            diagnostics.append("JOB_LIST_ITEM_ID_MISSING")
            continue
        if not title or not company:
            diagnostics.append("REQUIRED_JOB_LIST_FIELD_MISSING")
            continue
        jobs.append(
            BrowserJobSummary(
                external_job_id=external_id,
                title=title,
                company_name=company,
                detail_url=detail_url,
            )
        )
    if elements and not jobs:
        return _failure(
            page,
            platform,
            version,
            SessionStatus.SESSION_PAGE_CHANGED,
            "NO_RECOGNIZABLE_JOB_LIST_ITEM",
        )
    cursor = page.attribute(selectors.job_list_root, selectors.next_cursor_attribute)
    return _success(
        page, platform, version, PageType.JOB_LIST, jobs=jobs, cursor=cursor,
        reason_codes=list(dict.fromkeys(diagnostics)),
    )


def _extract_job(
    page: PageReader,
    platform: Platform,
    selectors: PlatformSelectors,
    version: str,
    expected_company: str | None,
    expected_job_title: str | None,
    fallback_company: str | None,
) -> ReadResult:
    title = page.text(selectors.job_title)
    company = _normalize_company_name(
        page.text(selectors.company) or fallback_company
    )
    description = page.text(selectors.description)
    if not title or not company or not description:
        return _failure(
            page,
            platform,
            version,
            SessionStatus.SESSION_PAGE_CHANGED,
            "REQUIRED_JOB_FIELD_MISSING",
        )
    if (expected_company and expected_company not in company) or (
        expected_job_title and expected_job_title not in title
    ):
        return _failure(
            page, platform, version, SessionStatus.SESSION_TARGET_MISMATCH, "JOB_TARGET_MISMATCH"
        )
    source_status = (
        "CLOSED"
        if page.exists(selectors.job_closed_marker)
        else ("OPEN" if page.exists(selectors.job_open_marker) else "UNKNOWN")
    )
    location = page.text(selectors.location)
    work_mode_evidence = "\n".join(
        value
        for element in page.elements(selectors.work_mode)
        if (value := element.text(""))
    ) or page.text(selectors.work_mode)
    work_mode = _normalize_work_mode(work_mode_evidence)
    if work_mode == "UNKNOWN":
        # BOSS 等平台常把“居家办公”只写在 JD 末尾，标题和属性栏不一定标注。
        work_mode = _normalize_work_mode(f"{title}\n{description}")
    job = BrowserJob(
        external_job_id=(
            page.attribute(selectors.job_id, "data-job-id") or _job_id_from_url(page.url)
        ),
        title=title,
        company_name=company,
        industry=page.text(selectors.industry),
        location=location,
        work_mode=work_mode,
        salary_text=page.text(selectors.salary),
        recruiter_name=_normalize_recruiter_name(page.text(selectors.recruiter_on_job)),
        recruiter_role=_normalize_recruiter_role(
            page.text(selectors.recruiter_role_on_job)
            if selectors.recruiter_role_on_job
            else None
        ),
        description=description,
        source_status=source_status,
    )
    return _success(page, platform, version, PageType.JOB, job=job)


def _normalize_recruiter_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.split())
    normalized = re.sub(
        r"\s*(?:刚刚|今日|本周|本月|\d+日内)?活跃$",
        "",
        normalized,
    ).strip()
    return normalized or None


def _normalize_recruiter_role(value: str | None) -> str:
    normalized = "".join((value or "").casefold().split())
    if any(marker in normalized for marker in ("猎头", "headhunter")):
        return "HEADHUNTER"
    return "DIRECT_EMPLOYER" if normalized else "UNKNOWN"


def _extract_conversation_list(
    page: PageReader,
    platform: Platform,
    selectors: PlatformSelectors,
    version: str,
) -> ReadResult:
    conversations: list[BrowserConversationSummary] = []
    diagnostics: list[str] = []
    elements = page.elements(selectors.conversation_list_items)
    for element in elements:
        raw_external_id = (
            element.attribute("", selectors.conversation_list_item_id_attribute)
            or element.attribute("", "d-c")
        )
        external_id = _stable_id(
            raw_external_id,
            selectors.conversation_list_item_id_json_key,
        )
        recruiter = element.text(selectors.conversation_list_item_recruiter)
        if not external_id or not recruiter:
            diagnostics.append("REQUIRED_CONVERSATION_LIST_FIELD_MISSING")
            continue
        unread = element.attribute("", selectors.conversation_list_item_unread_attribute)
        if not unread and selectors.conversation_list_item_unread_selector:
            unread = element.text(selectors.conversation_list_item_unread_selector)
        unread_count = _unread_count(unread)
        if unread_count is None:
            diagnostics.append("INVALID_UNREAD_COUNT")
            continue
        last_message_text = (
            element.text(selectors.conversation_list_item_last_message)
            if selectors.conversation_list_item_last_message
            else None
        )
        last_message_time_text = (
            element.text(selectors.conversation_list_item_last_message_time)
            if selectors.conversation_list_item_last_message_time
            else None
        )
        last_message_id = element.attribute(
            "", selectors.conversation_list_item_last_message_id_attribute
        )
        if not last_message_id and (last_message_text or last_message_time_text):
            last_message_id = _hash(
                f"{external_id}:{last_message_time_text or ''}:{last_message_text or ''}"
            )
        if selectors.conversation_list_requires_last_message_id and not last_message_id:
            diagnostics.append("REQUIRED_LAST_MESSAGE_ID_MISSING")
            continue
        job_title = element.text(selectors.conversation_list_item_job_title)
        company_name = element.text(selectors.conversation_list_item_company)
        if (
            selectors.conversation_list_item_job_title
            == selectors.conversation_list_item_company
            and selectors.conversation_company_separator
            and company_name
        ):
            parts = [
                part.strip()
                for part in company_name.split(
                    selectors.conversation_company_separator,
                    1,
                )
            ]
            company_name = parts[0]
            job_title = parts[1] if len(parts) > 1 else None
        conversations.append(
            BrowserConversationSummary(
                external_conversation_id=external_id,
                recruiter_name=recruiter,
                job_title=job_title,
                company_name=_normalize_company_name(company_name),
                external_job_id=element.attribute(
                    "", selectors.conversation_list_item_job_id_attribute
                ),
                last_message_id=last_message_id,
                last_message_text=last_message_text,
                last_message_time_text=last_message_time_text,
                category=(
                    element.attribute("", selectors.conversation_list_item_category_attribute)
                    or "ALL"
                ),
                unread_count=unread_count,
                identity_reliable=bool(raw_external_id),
            )
        )
    if elements and not conversations:
        return _failure(
            page,
            platform,
            version,
            SessionStatus.SESSION_PAGE_CHANGED,
            "NO_RECOGNIZABLE_CONVERSATION_LIST_ITEM",
        )
    cursor = page.attribute(selectors.conversation_list_root, selectors.next_cursor_attribute)
    return _success(
        page,
        platform,
        version,
        PageType.CONVERSATION_LIST,
        conversations=conversations,
        cursor=cursor,
        reason_codes=list(dict.fromkeys(diagnostics)),
    )


def _extract_conversation(
    page: PageReader,
    platform: Platform,
    selectors: PlatformSelectors,
    version: str,
    expected_recruiter: str | None,
) -> ReadResult:
    raw_conversation_id = page.attribute(
        selectors.conversation_id, selectors.conversation_id_attribute
    )
    conversation_id = _stable_id(
        raw_conversation_id,
        selectors.conversation_id_json_key,
    )
    recruiter = page.text(selectors.recruiter)
    if not conversation_id or not recruiter:
        return _failure(
            page,
            platform,
            version,
            SessionStatus.SESSION_PAGE_CHANGED,
            "REQUIRED_CONVERSATION_FIELD_MISSING",
        )
    if expected_recruiter and expected_recruiter not in recruiter:
        return _failure(
            page,
            platform,
            version,
            SessionStatus.SESSION_TARGET_MISMATCH,
            "RECRUITER_TARGET_MISMATCH",
        )
    messages: list[BrowserMessage] = []
    message_diagnostics: list[str] = []
    for element in page.elements(selectors.message_items):
        content = element.text(selectors.message_content)
        if not content:
            continue
        raw_time = element.attribute("", selectors.message_time_attribute)
        try:
            received_at = datetime.fromisoformat(raw_time) if raw_time else datetime.now(UTC)
        except ValueError:
            message_diagnostics.append("INVALID_MESSAGE_TIME")
            continue
        direction_value = element.attribute("", selectors.message_direction_attribute)
        style_value = element.attribute("", "style")
        class_names = (element.attribute("", "class") or "").split()
        direction = (
            MessageDirection.OUTBOUND
            if (
                direction_value == "outbound"
                or "row-reverse" in (style_value or "")
                or selectors.message_outbound_class in class_names
            )
            else MessageDirection.INBOUND
        )
        platform_message_id = element.attribute("", selectors.message_id_attribute)
        normalized_content = " ".join(content.split())
        external_id = platform_message_id or _hash(
            f"{conversation_id}:{direction.value}:{raw_time or 'UNKNOWN'}:{normalized_content}"
        )
        messages.append(
            BrowserMessage(
                external_message_id=external_id,
                content=content,
                received_at=received_at,
                direction=direction,
                identity_reliable=bool(platform_message_id or raw_time),
            )
        )
    company = page.text(selectors.conversation_company or selectors.company)
    if company and selectors.conversation_company_separator:
        company = company.split(selectors.conversation_company_separator, 1)[0].strip()
    platform_consents: list[BrowserPlatformConsent] = []
    if selectors.consent_cards and selectors.consent_card_title:
        for element in page.elements(selectors.consent_cards):
            prompt = element.text(selectors.consent_card_title)
            if prompt == "我想要一份您的附件简历，您是否同意":
                consent_type = PlatformConsentType.RESUME
            elif prompt == "我想要和您交换联系方式，您是否同意":
                consent_type = PlatformConsentType.CONTACT
            else:
                continue
            agree_text = (
                element.text(selectors.consent_card_buttons)
                if selectors.consent_card_buttons
                else None
            )
            classes = (
                (element.attribute(selectors.consent_card_buttons, "class") or "").split()
                if selectors.consent_card_buttons
                else []
            )
            platform_consents.append(
                BrowserPlatformConsent(
                    external_consent_id=_hash(f"{conversation_id}:{consent_type.value}:{prompt}"),
                    consent_type=consent_type,
                    prompt=prompt,
                    pending=agree_text == "同意" and "disabled" not in classes,
                )
            )
    if (
        selectors.location_consent_cards
        and selectors.location_consent_title
        and selectors.location_consent_detail
        and selectors.location_consent_button
    ):
        for element in page.elements(selectors.location_consent_cards):
            prompt = element.text(selectors.location_consent_title)
            if prompt != "您是否接受此工作地点?":
                continue
            address = element.attribute(
                selectors.location_consent_detail,
                "aria-label",
            ) or element.text(selectors.location_consent_detail)
            if not address:
                continue
            accept_text = element.text(selectors.location_consent_button)
            classes = (
                element.attribute(selectors.location_consent_button, "class")
                or ""
            ).split()
            platform_consents.append(
                BrowserPlatformConsent(
                    external_consent_id=_hash(
                        f"{conversation_id}:LOCATION:{address}"
                    ),
                    consent_type=PlatformConsentType.LOCATION,
                    prompt=prompt,
                    detail=address,
                    pending=(
                        accept_text == "可以接受"
                        and "disabled" not in classes
                    ),
                )
            )
    conversation = BrowserConversation(
        external_conversation_id=conversation_id,
        recruiter_name=recruiter,
        job_title=page.text(selectors.conversation_job_title),
        company_name=_normalize_company_name(company),
        external_job_id=page.attribute(selectors.conversation_root, "data-job-id"),
        messages=messages,
        platform_consents=platform_consents,
        identity_reliable=bool(raw_conversation_id),
    )
    return _success(
        page, platform, version, PageType.CONVERSATION,
        conversation=conversation,
        reason_codes=list(dict.fromkeys(message_diagnostics)),
    )


def _success(
    page: PageReader,
    platform: Platform,
    version: str,
    page_type: PageType,
    job: BrowserJob | None = None,
    conversation: BrowserConversation | None = None,
    jobs: list[BrowserJobSummary] | None = None,
    conversations: list[BrowserConversationSummary] | None = None,
    cursor: str | None = None,
    reason_codes: list[str] | None = None,
) -> ReadResult:
    return ReadResult(
        platform=platform,
        status=SessionStatus.SESSION_READY,
        page_type=page_type,
        page_url=page.url,
        page_title=page.title,
        content_hash=_evidence_hash(page, job, conversation),
        selector_version=version,
        job=job,
        conversation=conversation,
        jobs=jobs or [],
        conversations=conversations or [],
        cursor=cursor,
        reason_codes=reason_codes or [],
    )


def _failure(
    page: PageReader, platform: Platform, version: str, status: SessionStatus, reason: str
) -> ReadResult:
    return ReadResult(
        platform=platform,
        status=status,
        page_url=page.url,
        page_title=page.title,
        content_hash=_evidence_hash(page),
        selector_version=version,
        reason_codes=[reason],
    )


def _evidence_hash(page: PageReader, *values: object) -> str:
    return _hash("|".join([page.url, page.title, *(str(value) for value in values)]))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stable_id(raw_value: str | None, json_key: str | None) -> str | None:
    if not raw_value:
        return None
    if not json_key:
        return raw_value
    try:
        value = json.loads(unquote(raw_value)).get(json_key)
    except (json.JSONDecodeError, AttributeError):
        return None
    return str(value) if value is not None else None


def _unread_count(raw_value: str | None) -> int | None:
    if not raw_value:
        return 0
    try:
        metadata = json.loads(unquote(raw_value))
    except (json.JSONDecodeError, TypeError):
        metadata = None
    if isinstance(metadata, dict) and "unread" in metadata:
        unread = metadata["unread"]
        if isinstance(unread, bool):
            return int(unread)
        if isinstance(unread, int):
            return max(0, unread)
    match = re.search(r"\d+", raw_value)
    if match:
        return max(0, int(match.group(0)))
    return 1 if raw_value.strip() else 0


def _normalize_work_mode(value: str | None) -> str:
    lowered = (value or "").lower()
    if any(word in lowered for word in ("远程", "居家办公", "remote")):
        return "REMOTE"
    if any(word in lowered for word in ("混合", "hybrid")):
        return "HYBRID"
    if any(word in lowered for word in ("现场", "onsite", "坐班")):
        return "ONSITE"
    return "UNKNOWN"


def _normalize_company_name(value: str | None) -> str | None:
    if not value:
        return value
    normalized = " ".join(value.split()).lstrip("·•").strip()
    if normalized.startswith("公司名称"):
        return normalized.removeprefix("公司名称").strip()
    return normalized


def _job_id_from_url(value: str) -> str | None:
    parsed = urlparse(value)
    query_job_id = parse_qs(parsed.query).get("job_id", [None])[0]
    if query_job_id:
        return query_job_id
    liepin_match = re.search(r"/(?:job|a)/([^/?#]+?)\.shtml$", parsed.path)
    if liepin_match:
        path_id = liepin_match.group(1)
        # 猎聘企业职位路径使用 19 + 八位 job_id；首页和查询参数使用八位稳定 ID。
        # 详情加载后可能移除查询参数，因此路径回退也必须归一为同一身份。
        if (
            (parsed.hostname or "").lower().endswith("liepin.com")
            and parsed.path.startswith("/job/")
            and re.fullmatch(r"19\d{8}", path_id)
        ):
            return path_id[2:]
        return path_id
    name = parsed.path.rsplit("/", 1)[-1]
    for suffix in (".shtml", ".html"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name or None
