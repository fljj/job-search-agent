import hashlib
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from playwright.sync_api import Error as PlaywrightError
from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.browser.playwright_reader import (
    BossReadOnlyAdapter,
    LiepinReadOnlyAdapter,
    MaimaiReadOnlyAdapter,
    PlaywrightReadOnlyAdapter,
)
from apps.api.app.core.browser_config import get_browser_selectors
from apps.api.app.models import entities as db
from apps.api.app.schemas.browser import (
    BrowserReadRequest,
    BrowserReadResponse,
    PlatformSessionResponse,
)
from apps.api.app.schemas.conversation import ConversationPayload, MessagePayload
from apps.api.app.schemas.job import JobImportPayload
from apps.api.app.services.conversation_service import create_conversation, import_message
from apps.api.app.services.errors import DependencyUnavailableError
from apps.api.app.services.job_service import get_job_entity, import_job
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.browser_worker.models import MessageDirection, Platform, ReadResult, SessionStatus


def read_current_page(session: Session, payload: BrowserReadRequest) -> BrowserReadResponse:
    config = get_browser_selectors()
    adapters: dict[Platform, PlaywrightReadOnlyAdapter] = {
        Platform.BOSS: BossReadOnlyAdapter(config),
        Platform.MAIMAI: MaimaiReadOnlyAdapter(config),
        Platform.LIEPIN: LiepinReadOnlyAdapter(config),
    }
    adapter = adapters[payload.platform]
    try:
        result = adapter.read_current_page(payload.cdp_url, payload.expected_company,
                                           payload.expected_job_title, payload.expected_recruiter)
    except PlaywrightError as exc:
        raise DependencyUnavailableError("无法连接本机浏览器调试会话") from exc
    return persist_read_result(session, payload, result)


def persist_read_result(
    session: Session, payload: BrowserReadRequest, result: ReadResult
) -> BrowserReadResponse:
    ensure_default_user(session)
    platform_session = _upsert_platform_session(session, payload, result)
    fingerprint = hashlib.sha256(
        f"{payload.platform.value}:{result.status}:{result.page_type}:{result.content_hash}:"
        f"{','.join(result.reason_codes)}".encode()
    ).hexdigest()
    existing = session.scalar(select(db.BrowserReadRun).where(
        db.BrowserReadRun.input_fingerprint == fingerprint
    ))
    if existing:
        return _response(session, existing, duplicate=True)
    run = db.BrowserReadRun(
        user_id=DEFAULT_USER_ID, platform_session_id=platform_session.id,
        platform=payload.platform.value, status=result.status.value,
        page_type=result.page_type.value if result.page_type else None,
        input_fingerprint=fingerprint, reason_codes=result.reason_codes,
        cursor=result.cursor,
        extracted_items=(
            [item.model_dump(mode="json") for item in result.jobs]
            or [item.model_dump(mode="json") for item in result.conversations]
            or ([result.job.model_dump(mode="json")] if result.job else [])
            or (
                [result.conversation.model_dump(mode="json")]
                if result.conversation
                else []
            )
        ),
    )
    session.add(run)
    session.flush()
    evidence = db.PageEvidence(browser_read_run_id=run.id,
                               page_url=_sanitize_url(result.page_url),
                               page_title=result.page_title[:500],
                               content_hash=result.content_hash,
                               selector_version=result.selector_version)
    session.add(evidence)
    session.flush()
    if result.status is SessionStatus.SESSION_READY:
        _import_extraction(session, payload, result, run)
    session.commit()
    session.refresh(run)
    return _response(session, run)


def list_platform_sessions(session: Session) -> list[PlatformSessionResponse]:
    rows = session.scalars(select(db.PlatformSession).where(
        db.PlatformSession.user_id == DEFAULT_USER_ID
    ).order_by(db.PlatformSession.platform)).all()
    return [PlatformSessionResponse(id=row.id, platform=row.platform, status=row.status,
                                    last_reason_codes=row.last_reason_codes) for row in rows]


def _import_extraction(
    session: Session, payload: BrowserReadRequest, result: ReadResult, run: db.BrowserReadRun
) -> None:
    if result.job:
        imported = import_job(session, JobImportPayload(
            external_job_id=result.job.external_job_id, source_url=result.page_url,
            title=result.job.title,
            company_name=result.job.company_name, industry=result.job.industry,
            location=result.job.location, work_mode=result.job.work_mode,
            salary_text=result.job.salary_text, description=result.job.description,
            source_status=result.job.source_status, source=payload.platform.value,
        ))
        run.imported_job_id = imported.job.id
        return
    if result.conversation:
        if payload.job_id is None:
            raise ValueError("读取对话页时必须提供 job_id 以校验归属")
        get_job_entity(session, payload.job_id)
        conversation_data = create_conversation(session, ConversationPayload(
            job_id=payload.job_id, platform=payload.platform.value,
            external_conversation_id=result.conversation.external_conversation_id,
            recruiter_name=result.conversation.recruiter_name,
        ))
        conversation_id = conversation_data["id"]
        if not isinstance(conversation_id, UUID):
            raise RuntimeError("对话服务返回了无效 ID")
        run.imported_conversation_id = conversation_id
        run.imported_message_ids = [str(import_message(
            session, conversation_id, MessagePayload(
                external_message_id=item.external_message_id, content=item.content,
                received_at=item.received_at,
            )
        ).id) for item in result.conversation.messages
            if item.direction is MessageDirection.INBOUND]


def _upsert_platform_session(
    session: Session, payload: BrowserReadRequest, result: ReadResult
) -> db.PlatformSession:
    record = session.scalar(select(db.PlatformSession).where(
        db.PlatformSession.user_id == DEFAULT_USER_ID,
        db.PlatformSession.platform == payload.platform.value,
    ))
    if record is None:
        record = db.PlatformSession(user_id=DEFAULT_USER_ID, platform=payload.platform.value)
        session.add(record)
    parsed = urlparse(payload.cdp_url)
    record.cdp_endpoint = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}"
    record.status = result.status.value
    record.last_reason_codes = result.reason_codes
    record.last_checked_at = datetime.now(UTC)
    session.flush()
    return record


def _response(session: Session, run: db.BrowserReadRun, duplicate: bool = False) -> BrowserReadResponse:
    evidence = session.scalar(select(db.PageEvidence).where(db.PageEvidence.browser_read_run_id == run.id))
    if evidence is None:
        raise RuntimeError("浏览器读取记录缺少证据")
    return BrowserReadResponse(id=run.id, platform=run.platform, status=run.status,
                               page_type=run.page_type, reason_codes=run.reason_codes,
                               cursor=run.cursor,
                               jobs=run.extracted_items if run.page_type == "JOB_LIST" else [],
                               conversations=(
                                   run.extracted_items
                                   if run.page_type == "CONVERSATION_LIST"
                                   else []
                               ),
                               imported_job_id=run.imported_job_id,
                               imported_conversation_id=run.imported_conversation_id,
                               imported_message_ids=run.imported_message_ids,
                               evidence_id=evidence.id, duplicate=duplicate)


def _sanitize_url(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
