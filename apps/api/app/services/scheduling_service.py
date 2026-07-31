import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.core.scheduling_config import get_scheduling_config
from apps.api.app.models import entities as db
from apps.api.app.schemas.scheduling import (
    ApproveScheduleRequest,
    CalendarEventPayload,
    SchedulingPreferencePayload,
)
from apps.api.app.services.action_service import execute_action
from apps.api.app.services.errors import ResourceNotFoundError, VersionConflictError
from apps.api.app.services.qualification_service import refresh_qualification
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.policy_engine.content_check import validate_edited_content
from packages.policy_engine.state_machine import ActionStatus, ActionType
from packages.scheduling.calendar import CalendarGateway, CalendarProviderUnavailable
from packages.scheduling.engine import check_calendar, parse_invitation, suggest_slots
from packages.scheduling.models import (
    CalendarBusySlot,
    CalendarStatus,
    EventType,
    ParsedInvitation,
    SchedulingConfig,
)


def get_preference(session: Session) -> dict[str, object]:
    item = session.scalar(select(db.SchedulingPreference).where(
        db.SchedulingPreference.user_id == DEFAULT_USER_ID))
    config = SchedulingConfig.model_validate(item.settings) if item else get_scheduling_config()
    return {**config.model_dump(mode="json"), "version": item.version if item else 1}


def save_preference(session: Session, payload: SchedulingPreferencePayload) -> dict[str, object]:
    ensure_default_user(session)
    item = session.scalar(select(db.SchedulingPreference).where(
        db.SchedulingPreference.user_id == DEFAULT_USER_ID))
    settings = payload.model_dump(exclude={"version"}, mode="json")
    if item is None:
        if payload.version != 1:
            raise VersionConflictError("排期配置版本冲突")
        item = db.SchedulingPreference(user_id=DEFAULT_USER_ID, settings=settings, version=1)
        session.add(item)
    else:
        if item.version != payload.version:
            raise VersionConflictError("排期配置版本冲突")
        item.settings = settings
        item.version += 1
    session.commit()
    return get_preference(session)


def import_calendar_event(session: Session, payload: CalendarEventPayload) -> dict[str, object]:
    ensure_default_user(session)
    existing = session.scalar(select(db.CalendarEvent).where(
        db.CalendarEvent.user_id == DEFAULT_USER_ID, db.CalendarEvent.provider == "MOCK",
        db.CalendarEvent.external_event_id == payload.external_event_id))
    if existing:
        return _event_response(existing)
    event = db.CalendarEvent(user_id=DEFAULT_USER_ID, provider="MOCK", source="IMPORTED",
                             **payload.model_dump())
    session.add(event)
    session.commit()
    session.refresh(event)
    return _event_response(event)


def analyze_invitation(session: Session, message_id: UUID,
                       calendar_available: bool,
                       gateway: CalendarGateway | None = None) -> dict[str, object]:
    existing = session.scalar(select(db.InterviewRequest).where(db.InterviewRequest.message_id == message_id))
    if existing:
        return _request_response(session, existing)
    message = session.get(db.Message, message_id)
    if message is None:
        raise ResourceNotFoundError("消息不存在")
    conversation = session.get(db.Conversation, message.conversation_id)
    if conversation is None or conversation.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("对话不存在")
    config = _config(session)
    parsed = parse_invitation(message.content, message.received_at, config)
    qualification, _ = refresh_qualification(
        session, conversation, message=message
    )
    if parsed.event_type is EventType.PHONE_CALL:
        if qualification.value not in {"ROUGH_MATCH", "FULL_MATCH"}:
            session.commit()
            raise ValueError("电话沟通前必须至少达到大致匹配")
    elif qualification.value != "FULL_MATCH":
        session.commit()
        raise ValueError("面试前必须完整了解岗位并达到完全匹配")
    _supersede_open_requests(session, conversation.id)
    _supersede_generic_time_confirmations(session, message.id)
    slots = _busy_slots(session)
    query_start, query_end = _calendar_query_range(parsed, message.received_at)
    calendar_provider = gateway.provider if gateway else "MOCK"
    if gateway:
        try:
            slots.extend(_provider_slots(gateway, parsed, message.received_at))
        except CalendarProviderUnavailable:
            calendar_available = False
    status = check_calendar(parsed, slots, config, calendar_available)
    candidates = suggest_slots(parsed, slots, config) if status in {
        CalendarStatus.CONFLICT, CalendarStatus.AMBIGUOUS, CalendarStatus.INCOMPLETE,
    } else []
    request = db.InterviewRequest(
        user_id=DEFAULT_USER_ID, conversation_id=conversation.id, message_id=message.id,
        event_type=parsed.event_type.value, source_text=message.content,
        start_at=parsed.start_at, end_at=parsed.end_at, timezone=parsed.timezone,
        duration_minutes=parsed.duration_minutes, location=parsed.location,
        parse_confidence=Decimal(str(parsed.confidence)), risk_codes=parsed.risk_codes,
        candidate_slots=[{"start_at": start.isoformat(), "end_at": end.isoformat()}
                         for start, end in candidates], status="PENDING_APPROVAL",
    )
    session.add(request)
    session.flush()
    check = db.CalendarCheck(
        interview_request_id=request.id,
        status=status.value,
        snapshot_version=_snapshot(session, slots),
        conflicts=_conflict_summaries(slots),
        provider=calendar_provider,
        query_start_at=query_start,
        query_end_at=query_end,
        timezone=parsed.timezone,
        query_evidence={"slot_count": len(slots), "calendar_available": calendar_available},
        checked_at=datetime.now(UTC),
    )
    session.add(check)
    session.flush()
    confirmation = db.ScheduleConfirmation(
        interview_request_id=request.id, calendar_check_id=check.id,
        reply_content=_suggested_reply(parsed, status, candidates),
        reply_source="HUMAN",
        idempotency_key=f"schedule:{message.id}",
        expires_at=datetime.now(UTC) + timedelta(minutes=config.confirmation_ttl_minutes),
    )
    session.add(confirmation)
    conversation.state = "SCHEDULING"
    _audit(session, "SCHEDULE_CONFIRMATION_CREATED", request.id, None,
           "PENDING_APPROVAL", [status.value])
    session.commit()
    return _request_response(session, request)


def list_requests(session: Session) -> list[dict[str, object]]:
    return [_request_response(session, item) for item in session.scalars(
        select(db.InterviewRequest).where(db.InterviewRequest.user_id == DEFAULT_USER_ID)
        .order_by(db.InterviewRequest.created_at.desc())).all()]


def approve_schedule(session: Session, request_id: UUID,
                     payload: ApproveScheduleRequest) -> dict[str, object]:
    request, check, confirmation = _bundle(session, request_id)
    if confirmation.status != "PENDING_APPROVAL":
        raise ValueError("排期任务不在待确认状态")
    if confirmation.expires_at < datetime.now(UTC):
        confirmation.status = "EXPIRED"
        session.commit()
        raise ValueError("排期确认已过期")
    if validate_edited_content(payload.reply_content):
        raise ValueError("排期回复包含不允许自动发送的敏感内容")
    selected_start = payload.selected_start_at or request.start_at
    selected_end = payload.selected_end_at or request.end_at
    if selected_start is None or selected_end is None:
        raise ValueError("必须选择明确的开始和结束时间")
    if selected_end - selected_start != timedelta(minutes=request.duration_minutes):
        raise ValueError("选择的时间长度与沟通类型默认时长不一致")
    if check.status in {
        CalendarStatus.CONFLICT.value,
        CalendarStatus.AMBIGUOUS.value,
        CalendarStatus.INCOMPLETE.value,
    }:
        candidates = {
            (item["start_at"], item["end_at"]) for item in request.candidate_slots
        }
        if (selected_start.isoformat(), selected_end.isoformat()) not in candidates:
            raise ValueError("冲突或不完整任务必须选择服务端建议的候选时间")
        if payload.create_calendar_event:
            raise ValueError("对方尚未确认改期候选时间，不能提前创建日历事件")
    confirmation.reply_content = payload.reply_content
    confirmation.selected_start_at = selected_start
    confirmation.selected_end_at = selected_end
    confirmation.create_calendar_event = payload.create_calendar_event
    confirmation.status = "APPROVED"
    request.status = "APPROVED"
    _audit(session, "SCHEDULE_APPROVED", confirmation.id, "PENDING_APPROVAL", "APPROVED", [])
    session.commit()
    return _request_response(session, request)


def reject_schedule(session: Session, request_id: UUID) -> dict[str, object]:
    request, _, confirmation = _bundle(session, request_id)
    if confirmation.status == "CANCELLED":
        return _request_response(session, request)
    if confirmation.status not in {"PENDING_APPROVAL", "APPROVED"}:
        raise ValueError("当前排期任务不能拒绝")
    before = confirmation.status
    confirmation.status = "CANCELLED"
    request.status = "CANCELLED"
    conversation = session.get(db.Conversation, request.conversation_id)
    if conversation and conversation.state == "SCHEDULING":
        conversation.state = "WAITING_RECRUITER"
    _audit(session, "SCHEDULE_REJECTED", confirmation.id, before, "CANCELLED", [])
    session.commit()
    return _request_response(session, request)


def execute_schedule(
    session: Session,
    request_id: UUID,
    cdp_url: str,
    gateway: CalendarGateway | None = None,
    calendar_available: bool = True,
) -> dict[str, object]:
    request, _, confirmation = _bundle(session, request_id)
    if confirmation.status == "SUCCEEDED" and confirmation.action_id:
        return _request_response(session, request)
    if confirmation.status != "APPROVED":
        raise ValueError("具体时间未经用户批准")
    if confirmation.expires_at < datetime.now(UTC):
        confirmation.status = "EXPIRED"
        request.status = "EXPIRED"
        session.commit()
        raise ValueError("排期确认已过期")
    conversation = session.get(db.Conversation, request.conversation_id)
    message = session.get(db.Message, request.message_id)
    if conversation is None or message is None:
        raise ResourceNotFoundError("排期目标对话不存在")
    qualification, _ = refresh_qualification(
        session, conversation, message=message
    )
    qualification_allowed = (
        qualification.value in {"ROUGH_MATCH", "FULL_MATCH"}
        if request.event_type == EventType.PHONE_CALL.value
        else qualification.value == "FULL_MATCH"
    )
    if not qualification_allowed:
        confirmation.status = "PENDING_APPROVAL"
        request.status = "PENDING_APPROVAL"
        _audit(
            session,
            "SCHEDULE_QUALIFICATION_CHANGED",
            confirmation.id,
            "APPROVED",
            "PENDING_APPROVAL",
            [qualification.value],
        )
        session.commit()
        raise ValueError("岗位资格状态已变化，需要重新确认")
    config = _config(session)
    status, slots = _recheck_selected(
        session, request, confirmation, config, gateway, calendar_available
    )
    selected_start = confirmation.selected_start_at
    selected_end = confirmation.selected_end_at
    latest_check = db.CalendarCheck(
        interview_request_id=request.id,
        status=status.value,
        snapshot_version=_snapshot(session, slots),
        conflicts=_conflict_summaries(slots),
        provider=gateway.provider if gateway else "MOCK",
        query_start_at=(selected_start - timedelta(days=1) if selected_start else None),
        query_end_at=(selected_end + timedelta(days=1) if selected_end else None),
        timezone=request.timezone,
        query_evidence={"slot_count": len(slots), "calendar_available": calendar_available},
        checked_at=datetime.now(UTC),
    )
    session.add(latest_check)
    session.flush()
    confirmation.calendar_check_id = latest_check.id
    if status is not CalendarStatus.AVAILABLE:
        confirmation.status = "PENDING_APPROVAL"
        request.status = "PENDING_APPROVAL"
        _audit(
            session, "SCHEDULE_RECHECK_BLOCKED", confirmation.id,
            "APPROVED", "PENDING_APPROVAL", [status.value],
        )
        session.commit()
        raise ValueError("发送前日历检查未通过，需要重新确认")
    action = _schedule_action(session, request, confirmation)
    confirmation.action_id = action.id
    session.commit()
    result = execute_action(session, action.id, cdp_url)
    confirmation.status = result.status
    request.status = result.status
    if result.status == "SUCCEEDED":
        conversation = session.get(db.Conversation, request.conversation_id)
        if conversation:
            conversation.state = "SCHEDULE_CONFIRMED"
        if confirmation.create_calendar_event:
            try:
                _create_confirmed_event(session, request, confirmation, gateway)
            except (CalendarProviderUnavailable, OSError, TimeoutError):
                confirmation.status = "CALENDAR_OUTCOME_UNKNOWN"
                request.status = "CALENDAR_OUTCOME_UNKNOWN"
                _audit(
                    session,
                    "CALENDAR_CREATE_OUTCOME_UNKNOWN",
                    confirmation.id,
                    "SUCCEEDED",
                    "CALENDAR_OUTCOME_UNKNOWN",
                    ["CALENDAR_RESULT_NOT_OBSERVED"],
                )
    session.commit()
    return _request_response(session, request)


def _schedule_action(session: Session, request: db.InterviewRequest,
                     confirmation: db.ScheduleConfirmation) -> db.ActionQueue:
    existing = session.scalar(select(db.ActionQueue).where(
        db.ActionQueue.idempotency_key == f"schedule-action:{confirmation.id}"))
    if existing:
        return existing
    conversation = session.get(db.Conversation, request.conversation_id)
    job = session.get(db.Job, conversation.job_id) if conversation else None
    if conversation is None:
        raise ResourceNotFoundError("排期目标对话不存在")
    company_name = (
        job.company_name if job else conversation.observed_company_name
    )
    job_title = job.title if job else conversation.observed_job_title
    if not company_name or not job_title:
        raise ResourceNotFoundError("排期目标职位身份不完整")
    fingerprint = hashlib.sha256(
        f"{conversation.id}:REPLY:{confirmation.reply_content}".encode()).hexdigest()
    duplicate = session.scalar(select(db.ActionQueue).where(db.ActionQueue.send_fingerprint == fingerprint))
    if duplicate:
        return duplicate
    draft = db.GeneratedDraft(
        user_id=DEFAULT_USER_ID, conversation_id=conversation.id, message_id=request.message_id,
        draft_type="REPLY", content=confirmation.reply_content,
        intents=["INTERVIEW_TIME"], fact_ids=[], confidence=1,
        risk_codes=["SPECIFIC_TIME_USER_APPROVED"], input_fingerprint=fingerprint,
        generator_version="scheduling-v1",
    )
    session.add(draft)
    session.flush()
    decision = db.PolicyDecision(
        user_id=DEFAULT_USER_ID,
        draft_id=draft.id,
        action_type=ActionType.REPLY.value,
        decision="REQUIRE_CONFIRMATION",
        reason_codes=["SPECIFIC_TIME_USER_APPROVED"], policy_version="scheduling-policy-v1",
        input_snapshot={"schedule_confirmation_id": str(confirmation.id)},
    )
    session.add(decision)
    session.flush()
    task = db.ConfirmationTask(user_id=DEFAULT_USER_ID, decision_id=decision.id,
                               status="APPROVED", expires_at=confirmation.expires_at)
    session.add(task)
    session.flush()
    action = db.ActionQueue(
        user_id=DEFAULT_USER_ID, confirmation_task_id=task.id, policy_decision_id=decision.id,
        authorization_source="MANUAL", conversation_id=conversation.id, draft_id=draft.id,
        action_type=ActionType.REPLY.value,
        status=ActionStatus.APPROVED.value,
        content=confirmation.reply_content, platform=conversation.platform,
        target_company=company_name, target_job_title=job_title,
        target_recruiter=conversation.recruiter_name,
        target_conversation_key=conversation.external_conversation_id,
        idempotency_key=f"schedule-action:{confirmation.id}", send_fingerprint=fingerprint,
        approved_at=datetime.now(UTC),
    )
    session.add(action)
    session.flush()
    return action


def _recheck_selected(session: Session, request: db.InterviewRequest,
                      confirmation: db.ScheduleConfirmation,
                      config: SchedulingConfig,
                      gateway: CalendarGateway | None,
                      calendar_available: bool,
                      ) -> tuple[CalendarStatus, list[CalendarBusySlot]]:
    slots = _busy_slots(session)
    if not calendar_available:
        return CalendarStatus.UNAVAILABLE, slots
    if confirmation.selected_start_at is None or confirmation.selected_end_at is None:
        return CalendarStatus.AMBIGUOUS, slots
    parsed = ParsedInvitation(event_type=request.event_type,
        start_at=confirmation.selected_start_at, end_at=confirmation.selected_end_at,
        timezone=request.timezone, duration_minutes=request.duration_minutes,
        source_text=request.source_text, confidence=float(request.parse_confidence))
    if gateway:
        try:
            slots.extend(
                gateway.list_busy(
                    confirmation.selected_start_at - timedelta(days=1),
                    confirmation.selected_end_at + timedelta(days=1),
                    request.timezone,
                )
            )
        except CalendarProviderUnavailable:
            return CalendarStatus.UNAVAILABLE, slots
    return check_calendar(parsed, slots, config), slots


def _create_confirmed_event(session: Session, request: db.InterviewRequest,
                            confirmation: db.ScheduleConfirmation,
                            gateway: CalendarGateway | None) -> None:
    if confirmation.selected_start_at is None or confirmation.selected_end_at is None:
        return
    external_id = (
        gateway.create_event(
            idempotency_key=f"schedule:{confirmation.id}",
            title=f"求职沟通：{request.event_type}",
            start_at=confirmation.selected_start_at,
            end_at=confirmation.selected_end_at,
            timezone=request.timezone,
        )
        if gateway
        else f"schedule-{confirmation.id}"
    )
    provider = gateway.provider if gateway else "MOCK"
    if session.scalar(select(db.CalendarEvent).where(
        db.CalendarEvent.user_id == DEFAULT_USER_ID,
        db.CalendarEvent.provider == provider,
        db.CalendarEvent.external_event_id == external_id)):
        return
    session.add(db.CalendarEvent(
        user_id=DEFAULT_USER_ID, provider=provider, external_event_id=external_id,
        title=f"求职沟通：{request.event_type}", start_at=confirmation.selected_start_at,
        end_at=confirmation.selected_end_at, availability="BUSY", source="USER_AUTHORIZED",
    ))


def _bundle(session: Session, request_id: UUID) -> tuple[db.InterviewRequest, db.CalendarCheck, db.ScheduleConfirmation]:
    request = session.get(db.InterviewRequest, request_id)
    if request is None or request.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("排期请求不存在")
    check = session.scalar(select(db.CalendarCheck).where(db.CalendarCheck.interview_request_id == request.id)
                           .order_by(db.CalendarCheck.checked_at.desc()))
    confirmation = session.scalar(select(db.ScheduleConfirmation).where(
        db.ScheduleConfirmation.interview_request_id == request.id))
    if check is None or confirmation is None:
        raise RuntimeError("排期请求缺少日历检查或确认任务")
    return request, check, confirmation


def _supersede_open_requests(session: Session, conversation_id: UUID) -> None:
    rows = session.scalars(
        select(db.InterviewRequest).where(
            db.InterviewRequest.conversation_id == conversation_id,
            db.InterviewRequest.status.in_(["PENDING_APPROVAL", "APPROVED"]),
        )
    ).all()
    for request in rows:
        confirmation = session.scalar(
            select(db.ScheduleConfirmation).where(
                db.ScheduleConfirmation.interview_request_id == request.id
            )
        )
        before = request.status
        request.status = "SUPERSEDED"
        if confirmation:
            confirmation.status = "SUPERSEDED"
        _audit(
            session,
            "SCHEDULE_SUPERSEDED",
            request.id,
            before,
            "SUPERSEDED",
            ["NEW_SCHEDULING_MESSAGE"],
        )


def _supersede_generic_time_confirmations(
    session: Session, message_id: UUID
) -> None:
    tasks = session.scalars(
        select(db.ConfirmationTask)
        .join(db.PolicyDecision, db.PolicyDecision.id == db.ConfirmationTask.decision_id)
        .join(db.GeneratedDraft, db.GeneratedDraft.id == db.PolicyDecision.draft_id)
        .where(
            db.GeneratedDraft.message_id == message_id,
            db.ConfirmationTask.status == "PENDING_APPROVAL",
        )
    ).all()
    for task in tasks:
        task.status = "SUPERSEDED"


def _config(session: Session) -> SchedulingConfig:
    item = session.scalar(select(db.SchedulingPreference).where(
        db.SchedulingPreference.user_id == DEFAULT_USER_ID))
    return SchedulingConfig.model_validate(item.settings) if item else get_scheduling_config()


def _busy_slots(session: Session) -> list[CalendarBusySlot]:
    return [CalendarBusySlot(start_at=item.start_at, end_at=item.end_at,
                             availability=item.availability)
            for item in session.scalars(select(db.CalendarEvent).where(
                db.CalendarEvent.user_id == DEFAULT_USER_ID)).all()]


def _snapshot(
    session: Session, slots: list[CalendarBusySlot] | None = None
) -> str:
    raw = [
        (slot.start_at.isoformat(), slot.end_at.isoformat(), slot.availability)
        for slot in (slots if slots is not None else _busy_slots(session))
    ]
    return hashlib.sha256(json.dumps(raw).encode()).hexdigest()


def _provider_slots(
    gateway: CalendarGateway,
    parsed: ParsedInvitation,
    received_at: datetime,
) -> list[CalendarBusySlot]:
    start = parsed.start_at or received_at
    end = parsed.end_at or start + timedelta(days=14)
    return gateway.list_busy(
        start - timedelta(days=1),
        end + timedelta(days=1),
        parsed.timezone,
    )


def _calendar_query_range(
    parsed: ParsedInvitation,
    received_at: datetime,
) -> tuple[datetime, datetime]:
    start = parsed.start_at or received_at
    end = parsed.end_at or start + timedelta(days=14)
    return start - timedelta(days=1), end + timedelta(days=1)


def _conflict_summaries(
    slots: list[CalendarBusySlot],
) -> list[dict[str, object]]:
    return [
        {
            "start_at": slot.start_at.isoformat(),
            "end_at": slot.end_at.isoformat(),
            "availability": slot.availability,
        }
        for slot in slots
        if slot.availability in {"BUSY", "TENTATIVE", "OUT_OF_OFFICE"}
    ]


def _suggested_reply(parsed: ParsedInvitation, status: CalendarStatus,
                     candidates: list[tuple[datetime, datetime]]) -> str:
    if status is CalendarStatus.AVAILABLE and parsed.start_at:
        return f"这个时间我可以，感谢安排。确认时间为 {parsed.start_at:%Y-%m-%d %H:%M}（{parsed.timezone}）。"
    if status is CalendarStatus.CONFLICT and candidates:
        options = "、".join(start.strftime("%Y-%m-%d %H:%M") for start, _ in candidates)
        return f"原时间不便，以下时间是否可以：{options}？"
    if status is CalendarStatus.UNAVAILABLE:
        return "我需要先确认日程，稍后回复您具体时间。"
    return "方便补充明确的日期、时间和时区吗？我确认后尽快回复。"


def _request_response(session: Session, request: db.InterviewRequest) -> dict[str, object]:
    check = session.scalar(select(db.CalendarCheck).where(db.CalendarCheck.interview_request_id == request.id)
                           .order_by(db.CalendarCheck.checked_at.desc()))
    confirmation = session.scalar(select(db.ScheduleConfirmation).where(
        db.ScheduleConfirmation.interview_request_id == request.id))
    conversation = session.get(db.Conversation, request.conversation_id)
    job = (
        session.get(db.Job, conversation.job_id)
        if conversation and conversation.job_id
        else None
    )
    return {"id": request.id, "conversation_id": request.conversation_id,
            "platform": conversation.platform if conversation else None,
            "company_name": (
                job.company_name
                if job
                else conversation.observed_company_name if conversation else None
            ),
            "job_title": (
                job.title
                if job
                else conversation.observed_job_title if conversation else None
            ),
            "recruiter_name": (
                conversation.recruiter_name if conversation else None
            ),
            "qualification_status": (
                conversation.qualification_status if conversation else None
            ),
            "qualification_evidence": (
                conversation.qualification_evidence if conversation else []
            ),
            "event_type": request.event_type, "source_text": request.source_text,
            "start_at": request.start_at, "end_at": request.end_at,
            "timezone": request.timezone, "duration_minutes": request.duration_minutes,
            "confidence": float(request.parse_confidence), "risk_codes": request.risk_codes,
            "status": request.status, "calendar_status": check.status if check else None,
            "calendar_checked_at": check.checked_at if check else None,
            "candidate_slots": request.candidate_slots,
            "confirmation_id": confirmation.id if confirmation else None,
            "suggested_reply": confirmation.reply_content if confirmation else None,
            "create_calendar_event": confirmation.create_calendar_event if confirmation else False,
            "action_id": confirmation.action_id if confirmation else None}


def _event_response(event: db.CalendarEvent) -> dict[str, object]:
    return {"id": event.id, "external_event_id": event.external_event_id,
            "title": event.title, "start_at": event.start_at, "end_at": event.end_at,
            "availability": event.availability, "source": event.source}


def _audit(session: Session, event: str, entity_id: UUID, before: str | None,
           after: str, reasons: list[str]) -> None:
    session.add(db.AuditEvent(user_id=DEFAULT_USER_ID, actor_type="USER" if "APPROVED" in event else "SYSTEM",
        event_type=event, entity_type="scheduling", entity_id=entity_id,
        before_state=before, after_state=after, reason_codes=reasons,
        metadata_json={}, correlation_id=f"scheduling:{entity_id}"))
