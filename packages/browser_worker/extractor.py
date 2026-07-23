import hashlib
from datetime import UTC, datetime
from urllib.parse import urlparse

from packages.browser_worker.config import PlatformSelectors
from packages.browser_worker.models import (
    BrowserConversation,
    BrowserJob,
    BrowserMessage,
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)
from packages.browser_worker.ports import PageReader


def extract_current_page(
    page: PageReader, platform: Platform, selectors: PlatformSelectors, selector_version: str,
    expected_company: str | None = None, expected_job_title: str | None = None,
    expected_recruiter: str | None = None,
) -> ReadResult:
    host = (urlparse(page.url).hostname or "").lower()
    if host not in selectors.allowed_hosts:
        return _failure(page, platform, selector_version, SessionStatus.SESSION_TARGET_MISMATCH,
                        "UNSUPPORTED_HOST")
    if page.exists(selectors.verification_marker):
        return _failure(page, platform, selector_version, SessionStatus.SESSION_AUTH_REQUIRED,
                        "VERIFICATION_REQUIRED")
    if not page.exists(selectors.login_marker):
        return _failure(page, platform, selector_version, SessionStatus.SESSION_AUTH_REQUIRED,
                        "LOGIN_REQUIRED")
    if page.exists(selectors.job_root):
        return _extract_job(page, platform, selectors, selector_version,
                            expected_company, expected_job_title)
    if page.exists(selectors.conversation_root):
        return _extract_conversation(page, platform, selectors, selector_version, expected_recruiter)
    return _failure(page, platform, selector_version, SessionStatus.SESSION_PAGE_CHANGED,
                    "SUPPORTED_PAGE_ROOT_NOT_FOUND")


def _extract_job(
    page: PageReader, platform: Platform, selectors: PlatformSelectors, version: str,
    expected_company: str | None, expected_job_title: str | None,
) -> ReadResult:
    title = page.text(selectors.job_title)
    company = _normalize_company_name(page.text(selectors.company))
    description = page.text(selectors.description)
    if not title or not company or not description:
        return _failure(page, platform, version, SessionStatus.SESSION_PAGE_CHANGED,
                        "REQUIRED_JOB_FIELD_MISSING")
    if (expected_company and expected_company not in company) or (
        expected_job_title and expected_job_title not in title
    ):
        return _failure(page, platform, version, SessionStatus.SESSION_TARGET_MISMATCH,
                        "JOB_TARGET_MISMATCH")
    job = BrowserJob(external_job_id=page.attribute(selectors.job_id, "data-job-id"),
                     title=title, company_name=company, industry=page.text(selectors.industry),
                     location=page.text(selectors.location),
                     work_mode=_normalize_work_mode(page.text(selectors.work_mode)),
                     salary_text=page.text(selectors.salary), description=description)
    return _success(page, platform, version, PageType.JOB, job=job)


def _extract_conversation(
    page: PageReader, platform: Platform, selectors: PlatformSelectors, version: str,
    expected_recruiter: str | None,
) -> ReadResult:
    conversation_id = page.attribute(selectors.conversation_id, "data-conversation-id")
    recruiter = page.text(selectors.recruiter)
    if not conversation_id or not recruiter:
        return _failure(page, platform, version, SessionStatus.SESSION_PAGE_CHANGED,
                        "REQUIRED_CONVERSATION_FIELD_MISSING")
    if expected_recruiter and expected_recruiter not in recruiter:
        return _failure(page, platform, version, SessionStatus.SESSION_TARGET_MISMATCH,
                        "RECRUITER_TARGET_MISMATCH")
    messages: list[BrowserMessage] = []
    for index, element in enumerate(page.elements(selectors.message_items)):
        content = element.text(selectors.message_content)
        if not content:
            continue
        external_id = element.attribute("", selectors.message_id_attribute) or _hash(
            f"{conversation_id}:{index}:{content}"
        )
        messages.append(BrowserMessage(external_message_id=external_id, content=content,
                                       received_at=datetime.now(UTC)))
    conversation = BrowserConversation(external_conversation_id=conversation_id,
                                       recruiter_name=recruiter, messages=messages)
    return _success(page, platform, version, PageType.CONVERSATION, conversation=conversation)


def _success(page: PageReader, platform: Platform, version: str, page_type: PageType,
             job: BrowserJob | None = None,
             conversation: BrowserConversation | None = None) -> ReadResult:
    return ReadResult(platform=platform, status=SessionStatus.SESSION_READY,
                      page_type=page_type, page_url=page.url, page_title=page.title,
                      content_hash=_evidence_hash(page, job, conversation),
                      selector_version=version, job=job, conversation=conversation)


def _failure(page: PageReader, platform: Platform, version: str, status: SessionStatus,
             reason: str) -> ReadResult:
    return ReadResult(platform=platform, status=status, page_url=page.url,
                      page_title=page.title, content_hash=_evidence_hash(page),
                      selector_version=version, reason_codes=[reason])


def _evidence_hash(page: PageReader, *values: object) -> str:
    return _hash("|".join([page.url, page.title, *(str(value) for value in values)]))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _normalize_work_mode(value: str | None) -> str:
    lowered = (value or "").lower()
    if any(word in lowered for word in ("远程", "remote")):
        return "REMOTE"
    if any(word in lowered for word in ("混合", "hybrid")):
        return "HYBRID"
    if any(word in lowered for word in ("现场", "onsite", "坐班")):
        return "ONSITE"
    return "UNKNOWN"


def _normalize_company_name(value: str | None) -> str | None:
    if value and value.startswith("公司名称"):
        return value.removeprefix("公司名称").strip()
    return value
