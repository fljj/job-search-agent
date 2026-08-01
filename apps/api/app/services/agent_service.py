import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adapters.browser.fake_actions import FakeActionExecutor
from adapters.llm.errors import LlmProviderError
from apps.api.app.core.calendar import build_calendar_gateway
from apps.api.app.core.config import get_settings
from apps.api.app.models import entities as db
from apps.api.app.schemas.automation import AgentRunStartRequest, AutomationDispatchRequest
from apps.api.app.services.action_service import (
    PREWRITE_PAUSE_FAILURES,
    PREWRITE_RETRYABLE_FAILURES,
)
from apps.api.app.services.automation_service import _effective_rules, dispatch
from apps.api.app.services.conversation_service import create_reply_draft, create_resume_draft
from apps.api.app.services.errors import ResourceNotFoundError
from apps.api.app.services.llm_circuit_service import (
    llm_circuit_is_open,
    llm_circuit_status,
    open_llm_circuit,
)
from apps.api.app.services.llm_config_service import (
    build_runtime_llm_provider,
    runtime_settings,
)
from apps.api.app.services.scheduling_service import analyze_invitation
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.browser_worker.actions import ActionExecutor
from packages.llm.ports import LlmProvider
from packages.scoring.llm_engine import LlmScoreValidationError

logger = logging.getLogger(__name__)

SAFETY_FAILURE_CODES = {
    "CAPTCHA_REQUIRED",
    "LOGIN_REQUIRED",
    "SESSION_INVALID",
    "PAGE_STRUCTURE_CHANGED",
    "CONVERSATION_TARGET_MISMATCH",
    "JOB_TARGET_MISMATCH",
    "RESULT_NOT_OBSERVED",
}


def start_run(session: Session, payload: AgentRunStartRequest) -> dict[str, object]:
    ensure_default_user(session)
    strategy = session.get(db.JobStrategy, payload.strategy_id)
    if strategy is None or strategy.user_id != DEFAULT_USER_ID or not strategy.enabled:
        raise ResourceNotFoundError("启用的策略不存在")
    rules = _effective_rules(session, payload.platform, strategy.id)
    if not rules.enabled or rules.paused:
        raise ValueError("自动化未启用或已暂停")
    existing = session.scalar(
        select(db.AgentRun).where(
            db.AgentRun.user_id == DEFAULT_USER_ID,
            db.AgentRun.platform == payload.platform,
            db.AgentRun.status.in_(["RUNNING", "PAUSED"]),
        )
    )
    if existing:
        return _response(existing)
    run = db.AgentRun(
        user_id=DEFAULT_USER_ID,
        platform=payload.platform,
        strategy_id=strategy.id,
        status="RUNNING",
        heartbeat_at=datetime.now(UTC),
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        concurrent = session.scalar(
            select(db.AgentRun).where(
                db.AgentRun.user_id == DEFAULT_USER_ID,
                db.AgentRun.platform == payload.platform,
                db.AgentRun.status.in_(["RUNNING", "PAUSED"]),
            )
        )
        if concurrent:
            return _response(concurrent)
        raise
    _event(session, run.id, "RUN_STARTED")
    session.commit()
    session.refresh(run)
    return _response(run)


def get_run(session: Session, run_id: UUID) -> dict[str, object]:
    return _response(_get_run(session, run_id))


def list_runs(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(
        select(db.AgentRun)
        .where(db.AgentRun.user_id == DEFAULT_USER_ID)
        .order_by(db.AgentRun.created_at.desc())
    ).all()
    return [_response(row) for row in rows]


def pause_run(
    session: Session,
    run_id: UUID,
    reason_codes: list[str] | None = None,
) -> dict[str, object]:
    run = _get_run(session, run_id)
    if run.status == "PAUSED":
        return _response(run)
    if run.status != "RUNNING":
        raise ValueError("只有运行中的 Agent 可以暂停")
    run.status = "PAUSED"
    run.pause_reason_codes = reason_codes or ["USER_PAUSED"]
    _release_lease(session, run)
    run.version += 1
    _event(session, run.id, "RUN_PAUSED", reason_codes=run.pause_reason_codes)
    session.commit()
    return _response(run)


def resume_run(session: Session, run_id: UUID) -> dict[str, object]:
    run = _get_run(session, run_id)
    if run.status == "RUNNING":
        return _response(run)
    if run.status != "PAUSED":
        raise ValueError("只有已暂停的 Agent 可以恢复")
    if "RESULT_NOT_OBSERVED" in run.pause_reason_codes:
        unknown_action = session.scalar(
            select(db.ActionQueue.id).where(
                db.ActionQueue.platform == run.platform,
                db.ActionQueue.status == "OUTCOME_UNKNOWN",
            ).limit(1)
        )
        if unknown_action is not None:
            raise ValueError("仍有发送结果待对账，不能重新连接")
        platform_setting = session.scalar(
            select(db.AutomationSetting).where(
                db.AutomationSetting.user_id == DEFAULT_USER_ID,
                db.AutomationSetting.scope_type == "PLATFORM",
                db.AutomationSetting.scope_key == run.platform,
            )
        )
        if platform_setting is not None and platform_setting.paused:
            platform_setting.paused = False
            session.flush()
    rules = _effective_rules(session, run.platform, run.strategy_id)
    if not rules.enabled or rules.paused:
        raise ValueError("自动化配置仍处于关闭或暂停状态")
    run.status = "RUNNING"
    run.pause_reason_codes = []
    run.consecutive_failure_count = 0
    run.heartbeat_at = datetime.now(UTC)
    _release_lease(session, run)
    run.version += 1
    _event(session, run.id, "RUN_RESUMED")
    session.commit()
    return _response(run)


def tick_run(
    session: Session,
    run_id: UUID,
    worker_id: str,
    *,
    provider: LlmProvider | None = None,
    executor: ActionExecutor | None = None,
    now: datetime | None = None,
    execute_external_actions: bool = True,
) -> dict[str, object]:
    current = now or datetime.now(UTC)
    requested_run = _get_run(session, run_id)
    if requested_run.platform == "LIEPIN" and execute_external_actions:
        raise ValueError("猎聘 L3 只允许生成草稿和确认任务，禁止外部写动作")
    if executor is None and requested_run.platform != "MOCK":
        raise ValueError("真实平台 Agent 必须显式提供真实执行器")
    run = _acquire_lease(session, run_id, worker_id, current)
    settings = runtime_settings(session)
    rules = _effective_rules(session, run.platform, run.strategy_id)
    if not rules.enabled or rules.paused:
        return pause_run(session, run.id, ["AUTOMATION_DISABLED_OR_PAUSED"])
    if run.platform != "MOCK":
        platform_session = session.scalar(
            select(db.PlatformSession).where(
                db.PlatformSession.user_id == DEFAULT_USER_ID,
                db.PlatformSession.platform == run.platform,
            )
        )
        if platform_session is None or platform_session.status != "SESSION_READY":
            return pause_run(session, run.id, ["PLATFORM_SESSION_NOT_READY"])

    llm_provider = provider
    if llm_provider is None and not llm_circuit_is_open(session):
        try:
            llm_provider = build_runtime_llm_provider(session, settings)
        except LlmProviderError as exc:
            open_llm_circuit(session, settings, exc.code, now=current)
            session.commit()
    calendar_gateway = build_calendar_gateway(settings)
    action_executor = executor or FakeActionExecutor()
    processed = 0
    failure_count_before = run.failure_count
    session.execute(
        update(db.Message)
        .where(
            db.Message.status == "PROCESSING",
            db.Message.processing_started_at < current - timedelta(minutes=5),
        )
        .values(
            status="RETRY_WAIT",
            retry_at=current,
            error_code="STALE_MESSAGE_PROCESSING",
            processing_started_at=None,
        )
    )
    session.commit()
    messages = session.scalars(
        select(db.Message)
        .join(db.Conversation, db.Conversation.id == db.Message.conversation_id)
        .where(
            db.Conversation.platform == run.platform,
            or_(
                db.Conversation.strategy_id == run.strategy_id,
                db.Conversation.strategy_id.is_(None),
            ),
            ~db.Conversation.state.in_(
                ["ENDED", "DECLINED", "PAUSED", "OUTCOME_UNKNOWN"]
            ),
            db.Message.direction == "INBOUND",
            db.Message.identity_reliable.is_(True),
            or_(
                db.Message.status.in_(["RECEIVED", "WAITING_FOR_LLM"])
                if llm_provider is not None
                else db.Message.status == "RECEIVED",
                (
                    (db.Message.status == "RETRY_WAIT")
                    & (db.Message.retry_at <= current)
                ),
            ),
            ~select(db.GeneratedDraft.id)
            .where(db.GeneratedDraft.message_id == db.Message.id)
            .exists(),
        )
        .order_by(db.Message.created_at.asc())
        .limit(settings.agent_tick_batch_size)
    ).all()
    for message in messages:
        message.status = "PROCESSING"
        message.attempt_count += 1
        message.processing_started_at = current
        message.error_code = None
        session.commit()
        try:
            if "RESUME_REQUEST" in message.intents:
                resume = create_resume_draft(
                    session,
                    message.id,
                    llm_provider,
                    allow_provider_lookup=False,
                )
                if not execute_external_actions:
                    _disable_message_draft_dispatch(session, message.id)
                processed += 1
                _event(session, run.id, "RESUME_DECIDED", "draft", resume.id)
                _complete_message(session, message)
                continue
            draft = create_reply_draft(
                session,
                message.id,
                llm_provider,
                allow_provider_lookup=False,
            )
            if not execute_external_actions:
                _disable_message_draft_dispatch(session, message.id)
            processed += 1
            _event(session, run.id, "DRAFT_CREATED", "draft", draft.id)
            intent_values = [intent.value for intent in draft.intents]
            if (
                "INTERVIEW_TIME" in intent_values
                and any(
                    intent in intent_values
                    for intent in ("PHONE_CALL", "INTERVIEW_INVITATION")
                )
            ):
                schedule = analyze_invitation(
                    session,
                    message.id,
                    calendar_available=calendar_gateway is not None,
                    gateway=calendar_gateway,
                )
                _event(
                    session,
                    run.id,
                    "SCHEDULE_CONFIRMATION_CREATED",
                    "scheduling",
                    UUID(str(schedule["id"])),
                )
            else:
                resume = create_resume_draft(
                    session,
                    message.id,
                    llm_provider,
                    allow_provider_lookup=False,
                )
                _event(session, run.id, "RESUME_DECIDED", "draft", resume.id)
            if not execute_external_actions:
                _disable_message_draft_dispatch(session, message.id)
            _complete_message(session, message)
        except LlmProviderError as exc:
            open_llm_circuit(session, settings, exc.code, now=current)
            circuit = llm_circuit_status(session, settings)
            failure_code = str(circuit.get("failure_code") or exc.code)
            message.status = "WAITING_FOR_LLM"
            message.retry_at = None
            message.error_code = failure_code
            message.processing_started_at = None
            _record_failure(session, run, failure_code)
            session.commit()
            continue
        except LlmScoreValidationError as exc:
            message.status = "RETRY_WAIT"
            message.retry_at = current + timedelta(
                seconds=settings.boss_llm_retry_base_seconds
            )
            message.error_code = "INVALID_SCORING_OUTPUT"
            message.processing_started_at = None
            _record_failure(session, run, "INVALID_SCORING_OUTPUT")
            _event(
                session,
                run.id,
                "MESSAGE_SCORING_RETRY_SCHEDULED",
                "message",
                message.id,
                ["INVALID_SCORING_OUTPUT"],
                {"validation_error": str(exc)[:500]},
            )
            session.commit()
            continue
        except (ValueError, ResourceNotFoundError) as exc:
            message.status = "QUARANTINED"
            message.error_code = type(exc).__name__
            message.quarantined_at = current
            message.processing_started_at = None
            _record_failure(session, run, type(exc).__name__)
            _event(
                session,
                run.id,
                "MESSAGE_QUARANTINED",
                "message",
                message.id,
                [type(exc).__name__],
            )
            session.commit()

    drafts = (
        _pending_drafts(session, run, settings.agent_tick_batch_size)
        if execute_external_actions
        else []
    )
    for generated_draft, conversation, resume_id in drafts:
        if _has_unknown_outcome(session, conversation.id):
            _event(
                session,
                run.id,
                "ACTION_BLOCKED",
                "conversation",
                conversation.id,
                ["OUTCOME_UNKNOWN"],
            )
            continue
        action_type = generated_draft.draft_type
        payload = AutomationDispatchRequest(
            action_type=action_type,
            conversation_id=conversation.id,
            draft_id=generated_draft.id,
            resume_id=resume_id,
        )
        try:
            result = dispatch(
                session,
                payload,
                executor=action_executor,
                agent_run_id=run.id,
            )
        except (ValueError, ResourceNotFoundError) as exc:
            processed += 1
            run.failure_count += 1
            if _isolate_dispatch_error(
                session,
                run,
                generated_draft,
                current,
                exc,
            ):
                return _pause_after_failure(
                    session,
                    run,
                    ["DISPATCH_RESULT_UNKNOWN"],
                )
            continue
        processed += 1
        if not result.get("action_id") and result.get("decision") != "ALLOW_AUTO":
            _finish_retry_denied(
                session,
                run,
                generated_draft,
                current,
                result,
            )
        if result.get("action_id"):
            run.action_count += 1
            _event(
                session,
                run.id,
                "ACTION_EXECUTED",
                "action",
                UUID(str(result["action_id"])),
                [str(result.get("action_status"))],
            )
        if result.get("action_status") in {"FAILED_FINAL", "OUTCOME_UNKNOWN"}:
            code = str(result.get("failure_code") or result.get("action_status"))
            run.failure_count += 1
            reasons = [code] if code in SAFETY_FAILURE_CODES else [str(result["action_status"])]
            return _pause_after_failure(session, run, reasons)
        if (
            result.get("action_status") == "FAILED_RETRYABLE"
            and result.get("failure_code") in PREWRITE_PAUSE_FAILURES
        ):
            return _pause_after_failure(
                session, run, [str(result.get("failure_code"))]
            )

    run.processed_count += processed
    if run.failure_count == failure_count_before:
        run.consecutive_failure_count = 0
    run.heartbeat_at = current
    cursor = dict(run.cursor or {})
    cursor["last_tick_at"] = current.isoformat()
    run.cursor = cursor
    _release_lease(session, run)
    run.version += 1
    _event(session, run.id, "TICK_COMPLETED", metadata={"processed": processed})
    session.commit()
    return _response(run)


def _acquire_lease(
    session: Session, run_id: UUID, worker_id: str, now: datetime
) -> db.AgentRun:
    lease_seconds = get_settings().agent_lease_seconds
    claimed = session.scalar(
        update(db.AgentRun)
        .where(
            db.AgentRun.id == run_id,
            db.AgentRun.user_id == DEFAULT_USER_ID,
            db.AgentRun.status == "RUNNING",
            or_(
                db.AgentRun.lease_expires_at.is_(None),
                db.AgentRun.lease_expires_at <= now,
                db.AgentRun.lease_owner == worker_id,
            ),
        )
        .values(
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            version=db.AgentRun.version + 1,
        )
        .returning(db.AgentRun.id)
    )
    if claimed is None:
        run = _get_run(session, run_id)
        if run.status != "RUNNING":
            raise ValueError("Agent 未处于运行状态")
        raise ValueError("Agent 租约正由其他工作进程持有")
    session.commit()
    return _get_run(session, claimed)


def _pending_drafts(
    session: Session, run: db.AgentRun, limit: int
) -> list[tuple[db.GeneratedDraft, db.Conversation, UUID | None]]:
    retry_cutoff = datetime.now(UTC) - timedelta(seconds=60)
    selected_retry_id = session.scalar(
        select(db.ActionQueue.id)
        .join(
            db.Conversation,
            db.Conversation.id == db.ActionQueue.conversation_id,
        )
        .where(
            db.Conversation.platform == run.platform,
            db.Conversation.strategy_id == run.strategy_id,
            ~db.Conversation.state.in_(
                ["ENDED", "DECLINED", "PAUSED", "OUTCOME_UNKNOWN"]
            ),
            db.ActionQueue.status == "FAILED_RETRYABLE",
            db.ActionQueue.failure_code.in_(PREWRITE_RETRYABLE_FAILURES),
            db.ActionQueue.updated_at <= retry_cutoff,
            db.ActionQueue.draft_id.is_not(None),
        )
        .order_by(db.ActionQueue.updated_at.asc(), db.ActionQueue.id.asc())
        .limit(1)
    )
    drafts = session.scalars(
        select(db.GeneratedDraft)
        .join(db.Conversation, db.Conversation.id == db.GeneratedDraft.conversation_id)
        .where(
            db.Conversation.platform == run.platform,
            db.GeneratedDraft.dispatch_enabled.is_(True),
            ~db.Conversation.state.in_(
                ["ENDED", "DECLINED", "PAUSED", "OUTCOME_UNKNOWN"]
            ),
        )
        .order_by(db.GeneratedDraft.created_at.asc())
    ).all()
    result: list[tuple[db.GeneratedDraft, db.Conversation, UUID | None]] = []
    for draft in drafts:
        if len(result) >= limit:
            break
        score = session.get(db.JobScore, draft.job_score_id) if draft.job_score_id else None
        if score is not None and score.strategy_id != run.strategy_id:
            continue
        if (
            score is None
            and draft.draft_type not in {"REPLY", "RESUME", "MISMATCH_DECLINE"}
        ):
            continue
        decision = session.scalar(
            select(db.PolicyDecision)
            .where(db.PolicyDecision.draft_id == draft.id)
            .order_by(db.PolicyDecision.created_at.asc())
        )
        if decision is None or decision.decision != "ALLOW_AUTO":
            continue
        existing_action = session.scalar(
            select(db.ActionQueue).where(
                db.ActionQueue.draft_id == draft.id
            )
        )
        if existing_action is not None:
            if existing_action.id != selected_retry_id:
                continue
        conversation = session.get(db.Conversation, draft.conversation_id)
        if conversation is None:
            continue
        if conversation.strategy_id != run.strategy_id:
            continue
        if _draft_has_later_platform_reply(session, draft):
            continue
        raw_resume_id = decision.input_snapshot.get("resume_id")
        resume_id = UUID(str(raw_resume_id)) if raw_resume_id else None
        equivalent_action_id = session.scalar(
            select(db.ActionQueue.id).where(
                db.ActionQueue.conversation_id == conversation.id,
                db.ActionQueue.action_type == draft.draft_type,
                db.ActionQueue.content == (None if resume_id else draft.content),
                db.ActionQueue.resume_id == resume_id,
            )
        )
        if (
            equivalent_action_id is not None
            and equivalent_action_id != selected_retry_id
        ):
            continue
        result.append((draft, conversation, resume_id))
    return result


def _disable_message_draft_dispatch(session: Session, message_id: UUID) -> None:
    """持久化 L3 只读边界，避免未来启用写动作时补发历史草稿。"""

    session.execute(
        update(db.GeneratedDraft)
        .where(db.GeneratedDraft.message_id == message_id)
        .values(dispatch_enabled=False)
    )


def _draft_has_later_platform_reply(
    session: Session, draft: db.GeneratedDraft
) -> bool:
    if draft.message_id is None:
        return False
    source = session.get(db.Message, draft.message_id)
    if source is None or source.direction != "INBOUND":
        return False
    return bool(
        session.scalar(
            select(db.Message.id).where(
                db.Message.conversation_id == source.conversation_id,
                db.Message.direction == "OUTBOUND",
                db.Message.episode_number == source.episode_number,
                or_(
                    db.Message.received_at > source.received_at,
                    (
                        (db.Message.received_at == source.received_at)
                        & (db.Message.created_at > source.created_at)
                    ),
                ),
            )
        )
    )


def _has_unknown_outcome(session: Session, conversation_id: UUID) -> bool:
    return bool(
        session.scalar(
            select(db.ActionQueue.id).where(
                db.ActionQueue.conversation_id == conversation_id,
                db.ActionQueue.status == "OUTCOME_UNKNOWN",
            )
        )
    )


def _isolate_dispatch_error(
    session: Session,
    run: db.AgentRun,
    draft: db.GeneratedDraft,
    current: datetime,
    exc: ValueError | ResourceNotFoundError,
) -> bool:
    """隔离单条发送异常；返回 True 表示结果不明，必须暂停平台。"""
    action = session.scalar(
        select(db.ActionQueue).where(db.ActionQueue.draft_id == draft.id)
    )
    action_id = action.id if action is not None else None
    logger.exception(
        "ACTION_DISPATCH_FAILED action_id=%s draft_id=%s error=%s",
        action_id,
        draft.id,
        str(exc),
    )
    if action is None:
        decision = session.scalar(
            select(db.PolicyDecision).where(db.PolicyDecision.draft_id == draft.id)
        )
        if decision is not None:
            decision.decision = "DENY"
            decision.reason_codes = ["DISPATCH_DATA_ERROR"]
        _event(
            session,
            run.id,
            "ACTION_DISPATCH_FAILED",
            "draft",
            draft.id,
            [type(exc).__name__],
            {"error_message": str(exc)[:300]},
        )
        session.flush()
        return False
    if action.status == "EXECUTING":
        action.status = "OUTCOME_UNKNOWN"
        action.failure_code = "DISPATCH_RESULT_UNKNOWN"
        action.finished_at = current
        conversation = session.get(db.Conversation, action.conversation_id)
        if conversation is not None:
            conversation.state = "OUTCOME_UNKNOWN"
        _event(
            session,
            run.id,
            "ACTION_DISPATCH_OUTCOME_UNKNOWN",
            "action",
            action.id,
            ["DISPATCH_RESULT_UNKNOWN"],
            {"error_message": str(exc)[:300]},
        )
        session.flush()
        return True
    if action.status == "APPROVED":
        action.status = "FAILED_FINAL"
        action.failure_code = "DISPATCH_DATA_ERROR"
        action.finished_at = current
    if action.status == "FAILED_RETRYABLE":
        # 推迟当前失败动作，使下一轮可以选择队列中的下一条到期动作。
        action.updated_at = current
    _event(
        session,
        run.id,
        "ACTION_RETRY_DEFERRED",
        "action",
        action.id,
        [type(exc).__name__],
        {"error_message": str(exc)[:300]},
    )
    session.flush()
    return False


def _finish_retry_denied(
    session: Session,
    run: db.AgentRun,
    draft: db.GeneratedDraft,
    current: datetime,
    result: dict[str, object],
) -> None:
    action = session.scalar(
        select(db.ActionQueue).where(
            db.ActionQueue.draft_id == draft.id,
            db.ActionQueue.status == "FAILED_RETRYABLE",
        )
    )
    if action is None:
        return
    raw_reasons = result.get("reason_codes")
    reasons = (
        [str(reason) for reason in raw_reasons]
        if isinstance(raw_reasons, list)
        else []
    )
    action.status = "FAILED_FINAL"
    action.failure_code = "RETRY_POLICY_DENIED"
    action.finished_at = current
    _event(
        session,
        run.id,
        "ACTION_RETRY_DENIED",
        "action",
        action.id,
        reasons or ["RETRY_POLICY_DENIED"],
    )
    session.flush()


def _record_failure(session: Session, run: db.AgentRun, code: str) -> None:
    run.failure_count += 1
    run.consecutive_failure_count += 1
    _event(session, run.id, "TICK_FAILURE", reason_codes=[code])
    session.flush()


def _complete_message(session: Session, message: db.Message) -> None:
    message.status = "COMPLETED"
    message.retry_at = None
    message.error_code = None
    message.processing_started_at = None
    session.commit()


def _pause_after_failure(
    session: Session, run: db.AgentRun, reasons: list[str]
) -> dict[str, object]:
    run.status = "PAUSED"
    run.pause_reason_codes = reasons
    _release_lease(session, run)
    run.version += 1
    _event(session, run.id, "RUN_CIRCUIT_OPENED", reason_codes=reasons)
    session.commit()
    return _response(run)


def _finish_failed_tick(
    session: Session,
    run: db.AgentRun,
    current: datetime,
    reason: str,
) -> dict[str, object]:
    run.heartbeat_at = current
    _release_lease(session, run)
    run.version += 1
    _event(
        session,
        run.id,
        "TICK_COMPLETED_WITH_FAILURE",
        reason_codes=[reason],
    )
    session.commit()
    return _response(run)


def _release_lease(session: Session, run: db.AgentRun) -> None:
    session.execute(
        update(db.AgentRun)
        .where(db.AgentRun.id == run.id)
        .values(lease_owner=None, lease_expires_at=None)
    )
    run.lease_owner = None
    run.lease_expires_at = None


def _get_run(session: Session, run_id: UUID) -> db.AgentRun:
    run = session.get(db.AgentRun, run_id)
    if run is None or run.user_id != DEFAULT_USER_ID:
        raise ResourceNotFoundError("Agent 运行不存在")
    return run


def _event(
    session: Session,
    run_id: UUID,
    event_type: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    reason_codes: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        db.AgentRunEvent(
            agent_run_id=run_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            reason_codes=reason_codes or [],
            metadata_json=metadata or {},
        )
    )


def _response(run: db.AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "platform": run.platform,
        "strategy_id": run.strategy_id,
        "executor_type": run.executor_type,
        "status": run.status,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "lease_owner": run.lease_owner,
        "lease_expires_at": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "cursor": run.cursor,
        "processed_count": run.processed_count,
        "action_count": run.action_count,
        "failure_count": run.failure_count,
        "consecutive_failure_count": run.consecutive_failure_count,
        "pause_reason_codes": run.pause_reason_codes,
        "version": run.version,
    }
