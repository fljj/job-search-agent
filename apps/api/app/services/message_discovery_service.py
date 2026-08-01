import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from urllib.request import urlopen

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from adapters.browser.message_discovery import (
    DiscoveredConversation,
    MessageDiscoveryBatch,
)
from adapters.llm.errors import LlmProviderError
from apps.api.app.core.config import get_settings
from apps.api.app.models import entities as db
from apps.api.app.schemas.conversation import ConversationPayload, MessagePayload
from apps.api.app.schemas.job import JobImportPayload
from apps.api.app.schemas.score import ScoreRequest
from apps.api.app.services.action_service import execute_action
from apps.api.app.services.automation_service import (
    authorize_automatic_action,
    effective_rules,
)
from apps.api.app.services.conversation_service import (
    create_conversation,
    import_message,
)
from apps.api.app.services.job_service import import_job
from apps.api.app.services.llm_circuit_service import open_llm_circuit
from apps.api.app.services.llm_config_service import runtime_settings
from apps.api.app.services.qualification_service import refresh_qualification
from apps.api.app.services.score_service import create_score
from apps.api.app.services.user_service import DEFAULT_USER_ID
from packages.browser_worker.actions import ActionExecutor
from packages.browser_worker.models import (
    BrowserMessage,
    BrowserPlatformConsent,
    MessageDirection,
)
from packages.job_parser.normalizers import normalize_location
from packages.llm.ports import LlmProvider
from packages.policy_engine.automation import AutomationContext, AutomationDecision
from packages.policy_engine.state_machine import ActionType
from packages.scoring.llm_engine import LlmScoreValidationError

TERMINAL_CONVERSATION_STATES = {
    "ENDED",
    "DECLINED",
    "PAUSED",
    "OUTCOME_UNKNOWN",
}
UNSTABLE_CONVERSATION_RESCAN_INTERVAL = timedelta(minutes=60)
TERMINAL_REJECTION_MARKERS = (
    "不太合适",
    "不合适",
    "不太匹配",
    "不匹配",
    "不符合我们的要求",
    "不符合岗位要求",
    "这次先不继续",
    "先不继续沟通",
    "不再继续沟通",
    "暂时不推进",
    "暂不推进",
    "岗位已关闭",
    "岗位已经关闭",
    "岗位已招到",
    "岗位已经招到",
)
CONVERSATION_REOPEN_MARKERS = (
    "岗位符合我的方向",
    "这个岗位符合我的方向",
    "与我的方向匹配",
    "可以继续沟通",
    "希望继续沟通",
)


def process_next_inbound_job_score(
    session: Session,
    run: db.AgentRun,
    provider: LlmProvider,
    *,
    now: datetime | None = None,
) -> str:
    """每轮最多分析一个入站职位，硬性排除结果也保存为正式评分记录。"""
    current = now or datetime.now(UTC)
    root_cursor = dict(run.cursor or {})
    scoring_cursor = root_cursor.get("inbound_job_scoring")
    scoring_cursor = scoring_cursor if isinstance(scoring_cursor, dict) else {}
    raw_retry_at = scoring_cursor.get("next_retry_at")
    try:
        retry_at = datetime.fromisoformat(str(raw_retry_at)) if raw_retry_at else None
    except ValueError:
        retry_at = None
    conversation = session.scalar(
        select(db.Conversation)
        .where(
            db.Conversation.user_id == DEFAULT_USER_ID,
            db.Conversation.platform == run.platform,
            db.Conversation.strategy_id == run.strategy_id,
            db.Conversation.job_id.is_not(None),
            db.Conversation.latest_job_score_id.is_(None),
            db.Conversation.state == "ACTIVE",
        )
        .order_by(db.Conversation.updated_at.desc(), db.Conversation.id.desc())
        .limit(1)
    )
    if conversation is None or conversation.job_id is None:
        return "NONE"
    if (
        retry_at is not None
        and current < retry_at
        and scoring_cursor.get("conversation_id") == str(conversation.id)
    ):
        return "DEFERRED"
    strategy = session.get(db.JobStrategy, run.strategy_id)
    if strategy is None:
        return "NONE"
    try:
        score = create_score(
            session,
            conversation.job_id,
            ScoreRequest(
                strategy_id=strategy.id,
                candidate_profile_id=strategy.candidate_profile_id,
            ),
            provider=provider,
        )
    except LlmProviderError as exc:
        open_llm_circuit(session, runtime_settings(session), exc.code, now=current)
        _inbound_scoring_event(
            session, run, conversation, "INBOUND_JOB_SCORE_LLM_BLOCKED", [exc.code]
        )
        session.commit()
        return "LLM_BLOCKED"
    except LlmScoreValidationError:
        retry_at = current + timedelta(seconds=get_settings().boss_llm_retry_base_seconds)
        root_cursor["inbound_job_scoring"] = {
            "next_retry_at": retry_at.isoformat(),
            "conversation_id": str(conversation.id),
        }
        run.cursor = root_cursor
        _inbound_scoring_event(
            session,
            run,
            conversation,
            "INBOUND_JOB_SCORE_DEFERRED",
            ["INVALID_SCORING_OUTPUT"],
        )
        session.commit()
        return "DEFERRED"

    conversation.latest_job_score_id = score.id
    root_cursor.pop("inbound_job_scoring", None)
    run.cursor = root_cursor
    _inbound_scoring_event(
        session,
        run,
        conversation,
        "INBOUND_JOB_SCORED",
        ["HARD_FILTERED" if score.hard_rejected else "SCORED"],
    )
    session.commit()
    return "HARD_FILTERED" if score.hard_rejected else "SCORED"


def persist_discovery_batch(
    session: Session,
    run: db.AgentRun,
    worker_id: str,
    batch: MessageDiscoveryBatch,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    if run.platform != batch.platform.value:
        raise ValueError("消息发现批次平台与 Agent 运行不匹配")
    current = now or datetime.now(UTC)
    counts = {"discovered": len(batch.items), "imported": 0, "paused": 0, "skipped": 0}
    for item in batch.items:
        _record_state_sequence(
            session,
            run,
            item,
            [
                "OPENING_CONVERSATION",
                "VERIFYING_TARGET",
                "READING_MESSAGES",
                "BINDING_JOB",
            ],
        )
        if "PLATFORM_RECOMMENDATION_EXCLUDED" in item.reason_codes:
            counts["skipped"] += 1
            continue
        if item.detail is None or item.reason_codes:
            _discovery_event(
                session,
                run,
                item,
                item.reason_codes or ["CONVERSATION_DETAIL_MISSING"],
            )
            counts["paused"] += 1
            continue
        job = _resolve_job(session, batch, item)
        score = _current_score(session, run, job) if job else None
        conversation_data = create_conversation(
            session,
            ConversationPayload(
                job_id=job.id if job else None,
                external_conversation_id=item.summary.external_conversation_id,
                recruiter_name=item.summary.recruiter_name,
                platform=batch.platform.value,
                recruiter_role=_recruiter_role(item, job),
                identity_reliable=(
                    item.summary.identity_reliable
                    and bool(item.detail.conversation and item.detail.conversation.identity_reliable)
                ),
            ),
        )
        conversation = session.get(db.Conversation, conversation_data["id"])
        if conversation is None:
            raise RuntimeError("消息发现创建对话失败")
        if not _acquire_conversation_lease(session, conversation.id, worker_id, current):
            counts["skipped"] += 1
            continue
        detail = item.detail.conversation
        if detail is None:
            counts["paused"] += 1
            continue
        conversation.identity_reliable = (
            item.summary.identity_reliable and detail.identity_reliable
        )
        conversation.job_id = job.id if job else None
        conversation.strategy_id = run.strategy_id
        conversation.latest_job_score_id = score.id if score else None
        conversation.observed_company_name = detail.company_name or item.summary.company_name
        conversation.observed_job_title = detail.job_title or item.summary.job_title
        previous_external_job_id = conversation.observed_external_job_id
        conversation.observed_external_job_id = (
            detail.external_job_id or item.summary.external_job_id
        )
        known_message_ids = set(
            session.scalars(
                select(db.Message.external_message_id).where(
                    db.Message.conversation_id == conversation.id
                )
            ).all()
        )
        new_messages = [
            message for message in detail.messages
            if message.external_message_id not in known_message_ids
        ]
        if conversation.state in TERMINAL_CONVERSATION_STATES:
            if not new_messages:
                conversation.processing_lease_owner = None
                conversation.processing_lease_expires_at = None
                counts["skipped"] += 1
                continue
            if _starts_new_episode(previous_external_job_id, item, new_messages):
                conversation.episode_number += 1
                conversation.state = "ACTIVE"
                conversation.terminal_message_id = None
                conversation.terminal_at = None
            else:
                conversation.processing_lease_owner = None
                conversation.processing_lease_expires_at = None
                counts["skipped"] += 1
                continue
        else:
            conversation.state = "ACTIVE"
        _record_state_sequence(session, run, item, ["DECIDING"])
        for message in detail.messages:
            before = session.scalar(
                select(db.Message.id).where(
                    db.Message.conversation_id == conversation.id,
                    db.Message.external_message_id == message.external_message_id,
                )
            )
            import_message(
                session,
                conversation.id,
                MessagePayload(
                    external_message_id=message.external_message_id,
                    content=message.content,
                    received_at=message.received_at,
                    identity_reliable=message.identity_reliable,
                    direction=message.direction.value,
                ),
            )
            if before is None:
                counts["imported"] += 1
        terminal_state = _terminal_state_from_messages(new_messages)
        if terminal_state is not None:
            state, reason_code, terminal_message = terminal_state
            before_state = conversation.state
            conversation.state = state
            conversation.terminal_message_id = terminal_message.external_message_id
            conversation.terminal_at = terminal_message.received_at
            conversation.processing_lease_owner = None
            conversation.processing_lease_expires_at = None
            session.add(
                db.AuditEvent(
                    user_id=DEFAULT_USER_ID,
                    actor_type="SYSTEM",
                    event_type="CONVERSATION_TERMINATED",
                    entity_type="conversation",
                    entity_id=conversation.id,
                    before_state=before_state,
                    after_state=state,
                    reason_codes=[reason_code],
                    metadata_json={"platform": conversation.platform},
                    correlation_id=f"conversation-terminal:{conversation.id}",
                )
            )
            counts["skipped"] += 1
            _discovery_event(session, run, item, [reason_code])
            continue
        latest_inbound = session.scalar(
            select(db.Message)
            .where(
                db.Message.conversation_id == conversation.id,
                db.Message.direction == "INBOUND",
            )
            .order_by(db.Message.received_at.desc(), db.Message.created_at.desc())
            .limit(1)
        )
        if latest_inbound is not None:
            # 既有消息可能早于完整 JD 入库；职位重新绑定后必须用新事实刷新资格。
            refresh_qualification(session, conversation, message=latest_inbound)
        _queue_platform_consents(
            session,
            run,
            conversation,
            job,
            detail.platform_consents,
            current,
        )
        conversation.processing_lease_owner = None
        conversation.processing_lease_expires_at = None
        reasons = ["CONVERSATION_IMPORTED"]
        if job is None:
            reasons.append("JOB_UNBOUND")
        elif score is None:
            reasons.append("CURRENT_SCORE_MISSING")
        _discovery_event(session, run, item, reasons)
        _record_state_sequence(session, run, item, ["RETURNING_TO_LIST"])
    root_cursor = dict(run.cursor or {})
    previous_message_cursor = root_cursor.get("message_discovery")
    previous_message_cursor = (
        previous_message_cursor if isinstance(previous_message_cursor, dict) else {}
    )
    seen_message_keys, unstable_rescan_at = _next_seen_message_keys(
        batch,
        previous_message_cursor,
        current,
    )
    root_cursor["message_discovery"] = {
        "discovery_state": "LIST_READY",
        "partition": batch.partition,
        "scroll_position": 0 if batch.exhausted else batch.scroll_position,
        "next_cursor": batch.next_cursor,
        "last_conversation_id": (
            batch.items[-1].summary.external_conversation_id if batch.items else None
        ),
        "last_message_id": (batch.items[-1].summary.last_message_id if batch.items else None),
        "last_scan_at": batch.scanned_at.isoformat(),
        "seen_message_keys": seen_message_keys,
        "unstable_rescan_at": unstable_rescan_at.isoformat(),
        "exhausted": batch.exhausted,
    }
    run.cursor = root_cursor
    session.commit()
    return counts


def execute_pending_platform_consents(
    session: Session,
    run: db.AgentRun,
    cdp_url: str,
    executor: ActionExecutor,
) -> list[str]:
    rules = effective_rules(session, run.platform, run.strategy_id)
    if not rules.enabled or rules.paused or rules.emergency_stop:
        return []
    actions = session.scalars(
        select(db.ActionQueue)
        .where(
            db.ActionQueue.agent_run_id == run.id,
            db.ActionQueue.action_type.in_(
                [
                    "RESUME_CONSENT_ACCEPT",
                    "CONTACT_CONSENT_ACCEPT",
                    "LOCATION_CONSENT_ACCEPT",
                ]
            ),
            db.ActionQueue.status == "APPROVED",
        )
        .order_by(db.ActionQueue.created_at.asc())
        .limit(1)
    ).all()
    statuses = []
    for action in actions:
        if (
            action.action_type == ActionType.RESUME_CONSENT_ACCEPT.value
            and not rules.auto_resume_enabled
        ):
            continue
        if action.action_type in {
            "CONTACT_CONSENT_ACCEPT",
            "LOCATION_CONSENT_ACCEPT",
        } and not rules.auto_reply_enabled:
            continue
        result = execute_action(session, action.id, cdp_url, executor)
        statuses.append(result.status)
    return statuses


def _queue_platform_consents(
    session: Session,
    run: db.AgentRun,
    conversation: db.Conversation,
    job: db.Job | None,
    consents: list[BrowserPlatformConsent],
    current: datetime,
) -> None:
    score = (
        session.get(db.JobScore, conversation.latest_job_score_id)
        if conversation.latest_job_score_id
        else None
    )
    for consent in consents:
        if not consent.pending:
            continue
        safety_blockers: list[str] = []
        if not conversation.identity_reliable:
            safety_blockers.append("CONVERSATION_IDENTITY_UNRELIABLE")
        if score is not None and score.hard_rejected:
            safety_blockers.append("JOB_HARD_REJECTED")
        if consent.consent_type.value == "LOCATION":
            action_type = ActionType.LOCATION_CONSENT_ACCEPT.value
            location_allowed = bool(
                consent.detail
                and _location_consent_allowed(session, run, consent.detail)
            )
            if not location_allowed:
                safety_blockers.append("LOCATION_CONSENT_NOT_ALLOWED")
        elif consent.consent_type.value == "RESUME":
            action_type = ActionType.RESUME_CONSENT_ACCEPT.value
        else:
            action_type = ActionType.CONTACT_CONSENT_ACCEPT.value
        external_id = consent.external_consent_id
        fingerprint = hashlib.sha256(
            f"{conversation.id}:{action_type}:{external_id}".encode()
        ).hexdigest()
        if session.scalar(
            select(db.ActionQueue.id).where(db.ActionQueue.send_fingerprint == fingerprint)
        ):
            continue
        context = AutomationContext(
            action_type=action_type,
            score=score.total_score if score is not None else 0,
            grade=score.grade if score is not None else "UNKNOWN",
            eligible=not bool(safety_blockers),
            job_open=True,
            original_decision=(
                "DENY" if safety_blockers else "ALLOW_AUTO"
            ),
            explicit_resume_request=consent.consent_type.value == "RESUME",
            resume_available=True,
            qualification_status=conversation.qualification_status,
        )
        decision, reasons, policy = authorize_automatic_action(
            session,
            action_type=action_type,
            platform=conversation.platform,
            strategy_id=run.strategy_id,
            context=context,
            safety_blockers=safety_blockers,
            input_snapshot={
                "conversation_id": str(conversation.id),
                "consent_type": consent.consent_type.value,
                "external_consent_id": consent.external_consent_id,
                "prompt": consent.prompt,
                "detail": consent.detail,
            },
        )
        if decision is not AutomationDecision.ALLOW_AUTO:
            continue
        action = db.ActionQueue(
            user_id=DEFAULT_USER_ID,
            policy_decision_id=policy.id,
            strategy_id=run.strategy_id,
            agent_run_id=run.id,
            authorization_source="AUTO",
            authorization_basis="INBOUND_PLATFORM_CONSENT",
            qualification_snapshot={
                "status": conversation.qualification_status,
                "evidence": conversation.qualification_evidence,
                "version": conversation.qualification_version,
            },
            job_id=conversation.job_id,
            conversation_id=conversation.id,
            action_type=action_type,
            status="APPROVED",
            content=(
                consent.detail
                if consent.consent_type.value == "LOCATION"
                else consent.prompt
            ),
            platform=conversation.platform,
            target_company=(
                job.company_name if job else conversation.observed_company_name or "未知公司"
            ),
            target_job_title=(job.title if job else conversation.observed_job_title or "未知岗位"),
            target_recruiter=conversation.recruiter_name,
            target_conversation_key=conversation.external_conversation_id,
            idempotency_key=f"platform-consent:{fingerprint}",
            send_fingerprint=fingerprint,
            approved_at=current,
        )
        session.add(action)
        session.flush()
        session.add(
            db.AuditEvent(
                user_id=DEFAULT_USER_ID,
                actor_type="SYSTEM",
                event_type="PLATFORM_CONSENT_AUTO_APPROVED",
                entity_type="action",
                entity_id=action.id,
                before_state=None,
                after_state="APPROVED",
                reason_codes=["QUALIFIED_INBOUND_PLATFORM_CONSENT"],
                metadata_json={
                    "consent_type": consent.consent_type.value,
                    "policy_decision_id": str(policy.id),
                    "reason_codes": reasons,
                },
                correlation_id=f"platform-consent:{action.id}",
            )
        )


def _location_consent_allowed(
    session: Session,
    run: db.AgentRun,
    address: str,
) -> bool:
    strategy = session.get(db.JobStrategy, run.strategy_id)
    if strategy is None:
        return False
    normalized_address = normalize_location(address) or ""
    onsite_rule = next(
        (
            rule
            for rule in strategy.work_mode_rules
            if rule.work_mode == "ONSITE" and rule.enabled
        ),
        None,
    )
    if onsite_rule is None:
        return False
    return any(
        (normalize_location(location.location_name) or "")
        in normalized_address
        for location in onsite_rule.locations
        if normalize_location(location.location_name)
    )


def _terminal_state_from_messages(
    messages: list[BrowserMessage],
) -> tuple[str, str, BrowserMessage] | None:
    for message in reversed(messages):
        content = message.content
        normalized = "".join(content.split())
        if not normalized or any(mark in normalized for mark in ("吗", "？", "?")):
            continue
        if message.direction is MessageDirection.OUTBOUND and any(
            marker in normalized for marker in CONVERSATION_REOPEN_MARKERS
        ):
            return None
        if not any(marker in normalized for marker in TERMINAL_REJECTION_MARKERS):
            continue
        if message.direction is MessageDirection.OUTBOUND:
            return "DECLINED", "CANDIDATE_EXPLICITLY_DECLINED", message
        if message.direction is MessageDirection.INBOUND:
            return "ENDED", "RECRUITER_EXPLICITLY_DECLINED", message
    return None


def _starts_new_episode(
    previous_external_job_id: str | None,
    item: DiscoveredConversation,
    messages: list[BrowserMessage],
) -> bool:
    observed_job_id = (
        item.detail.conversation.external_job_id
        if item.detail and item.detail.conversation
        else item.summary.external_job_id
    )
    if observed_job_id and observed_job_id != previous_external_job_id:
        return True
    return any(
        any(marker in "".join(message.content.split()) for marker in CONVERSATION_REOPEN_MARKERS)
        for message in messages
    )


def _recruiter_role(
    item: DiscoveredConversation,
    job: db.Job | None = None,
) -> str:
    if job is not None and job.recruiter_role != "UNKNOWN":
        return job.recruiter_role
    if item.job_detail and item.job_detail.job:
        role = item.job_detail.job.recruiter_role
        if role != "UNKNOWN":
            return role
    title = "".join((item.summary.job_title or "").split())
    has_actual_job = bool(item.job_detail and item.job_detail.job)
    return "HEADHUNTER" if "猎头" in title and not has_actual_job else "DIRECT_EMPLOYER"


def _next_seen_message_keys(
    batch: MessageDiscoveryBatch,
    previous_cursor: dict[str, object],
    current: datetime,
) -> tuple[list[str], datetime]:
    raw_rescan_at = previous_cursor.get("unstable_rescan_at")
    try:
        previous_rescan_at = datetime.fromisoformat(str(raw_rescan_at)) if raw_rescan_at else None
    except ValueError:
        previous_rescan_at = None
    rescan_due = batch.exhausted and (
        previous_rescan_at is None
        or current - previous_rescan_at >= UNSTABLE_CONVERSATION_RESCAN_INTERVAL
    )
    if not rescan_due:
        return batch.seen_message_keys, previous_rescan_at or current
    # 平台同意卡片不一定改变列表最后消息 ID；定时释放去重键，才能重新读取卡片。
    # 终止会话仍由 scan 的 terminal_message_ids 排除，不会被重新打开。
    return ([], current)


def record_ready_platform_session(
    session: Session,
    run: db.AgentRun,
    cdp_url: str,
) -> None:
    """记录已识别的平台页面证据，供发现流程和后续动作复核。"""
    record = session.scalar(
        select(db.PlatformSession).where(
            db.PlatformSession.user_id == DEFAULT_USER_ID,
            db.PlatformSession.platform == run.platform,
        )
    )
    if record is None:
        record = db.PlatformSession(
            user_id=DEFAULT_USER_ID,
            platform=run.platform,
        )
        session.add(record)
    parsed = urlparse(cdp_url)
    record.cdp_endpoint = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}"
    record.status = "SESSION_READY"
    record.last_reason_codes = []
    record.last_checked_at = datetime.now(UTC)
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read())
        role_targets: dict[str, list[str]] = {}
        for target in targets:
            target_id = str(target.get("id") or "")
            role = _page_role(run.platform, str(target.get("url") or ""))
            if not target_id or role is None:
                continue
            role_targets.setdefault(role, []).append(target_id)
        for role, target_ids in role_targets.items():
            registration = session.scalar(
                select(db.BrowserPageRegistration).where(
                    db.BrowserPageRegistration.platform == run.platform,
                    db.BrowserPageRegistration.page_role == role,
                )
            )
            if registration is None:
                registration = db.BrowserPageRegistration(
                    platform=run.platform,
                    page_role=role,
                    target_id=target_ids[0],
                    agent_owned=False,
                    status="READY" if len(target_ids) == 1 else "AMBIGUOUS",
                    last_verified_at=datetime.now(UTC),
                )
                session.add(registration)
            else:
                registration.target_id = target_ids[0]
                registration.status = "READY" if len(target_ids) == 1 else "AMBIGUOUS"
                registration.last_verified_at = datetime.now(UTC)
    except (OSError, TimeoutError, ValueError):
        pass
    session.flush()


def record_platform_session_failure(
    session: Session,
    run: db.AgentRun,
    reason_code: str,
) -> None:
    record = session.scalar(select(db.PlatformSession).where(
        db.PlatformSession.user_id == DEFAULT_USER_ID,
        db.PlatformSession.platform == run.platform,
    ))
    if record is None:
        record = db.PlatformSession(
            user_id=DEFAULT_USER_ID,
            platform=run.platform,
            cdp_endpoint="http://127.0.0.1:9222",
            status="SESSION_UNAVAILABLE",
        )
        session.add(record)
    record.status = "SESSION_UNAVAILABLE"
    record.last_reason_codes = [reason_code]
    record.last_checked_at = datetime.now(UTC)
    session.flush()


def _page_role(platform: str, url: str) -> str | None:
    parsed = urlparse(url)
    path = parsed.path
    if platform == "BOSS":
        if path == "/web/geek/chat":
            return "MESSAGE_LIST"
        if path in {"/web/geek/jobs", "/web/geek/job"}:
            return "JOB_LIST"
    if platform == "MAIMAI" and "feed_im" in path:
        return "MESSAGE_LIST"
    if (
        platform == "LIEPIN"
        and parsed.hostname == "c.liepin.com"
        and path in {"", "/"}
    ):
        return "JOB_LIST"
    return None


def _resolve_job(
    session: Session,
    batch: MessageDiscoveryBatch,
    item: DiscoveredConversation,
) -> db.Job | None:
    if item.job_detail and item.job_detail.job:
        source = item.job_detail.job
        imported = import_job(
            session,
            JobImportPayload(
                external_job_id=source.external_job_id,
                title=source.title,
                company_name=source.company_name,
                industry=source.industry,
                location=source.location,
                work_mode=source.work_mode,
                salary_text=source.salary_text,
                description=source.description,
                source_status=source.source_status,
                source=batch.platform.value,
                recruiter_role=source.recruiter_role,
            ),
        )
        return session.get(db.Job, imported.job.id)
    detail = item.detail.conversation if item.detail else None
    external_job_id = item.summary.external_job_id or (detail.external_job_id if detail else None)
    if external_job_id:
        matches = session.scalars(
            select(db.Job).where(
                db.Job.user_id == DEFAULT_USER_ID,
                db.Job.source == batch.platform.value,
                db.Job.external_job_id == external_job_id,
            )
        ).all()
        return matches[0] if len(matches) == 1 else None
    title = item.summary.job_title or (detail.job_title if detail else None)
    company = item.summary.company_name or (detail.company_name if detail else None)
    if not title or not company:
        return None
    matches = session.scalars(
        select(db.Job).where(
            db.Job.user_id == DEFAULT_USER_ID,
            db.Job.source == batch.platform.value,
            db.Job.title == title,
            db.Job.company_name == company,
        )
    ).all()
    return matches[0] if len(matches) == 1 else None


def _current_score(session: Session, run: db.AgentRun, job: db.Job) -> db.JobScore | None:
    strategy = session.get(db.JobStrategy, run.strategy_id)
    if strategy is None:
        return None
    profile = session.get(db.CandidateProfile, strategy.candidate_profile_id)
    if profile is None:
        return None
    return session.scalar(
        select(db.JobScore)
        .where(
            db.JobScore.job_id == job.id,
            db.JobScore.strategy_id == run.strategy_id,
            db.JobScore.strategy_version == strategy.version,
            db.JobScore.profile_version == profile.version,
            db.JobScore.effective_job_status == "OPEN",
        )
        .order_by(db.JobScore.created_at.desc())
        .limit(1)
    )


def _acquire_conversation_lease(
    session: Session,
    conversation_id: object,
    worker_id: str,
    now: datetime,
) -> bool:
    claimed = session.scalar(
        update(db.Conversation)
        .where(
            db.Conversation.id == conversation_id,
            or_(
                db.Conversation.processing_lease_expires_at.is_(None),
                db.Conversation.processing_lease_expires_at <= now,
                db.Conversation.processing_lease_owner == worker_id,
            ),
        )
        .values(
            processing_lease_owner=worker_id,
            processing_lease_expires_at=now + timedelta(seconds=30),
        )
        .returning(db.Conversation.id)
    )
    session.flush()
    return claimed is not None


def _discovery_event(
    session: Session,
    run: db.AgentRun,
    item: DiscoveredConversation,
    reasons: list[str],
) -> None:
    session.add(
        db.AgentRunEvent(
            agent_run_id=run.id,
            event_type="CONVERSATION_DISCOVERY",
            entity_type="conversation",
            reason_codes=reasons,
            metadata_json={
                "external_conversation_id": item.summary.external_conversation_id,
                "recruiter_name": item.summary.recruiter_name,
                "job_title": item.summary.job_title,
            },
        )
    )


def _inbound_scoring_event(
    session: Session,
    run: db.AgentRun,
    conversation: db.Conversation,
    event_type: str,
    reasons: list[str],
) -> None:
    session.add(
        db.AgentRunEvent(
            agent_run_id=run.id,
            event_type=event_type,
            entity_type="conversation",
            reason_codes=reasons,
            metadata_json={
                "conversation_id": str(conversation.id),
                "job_id": str(conversation.job_id),
            },
        )
    )


def _record_state_sequence(
    session: Session,
    run: db.AgentRun,
    item: DiscoveredConversation,
    states: list[str],
) -> None:
    for state in states:
        session.add(
            db.AgentRunEvent(
                agent_run_id=run.id,
                event_type="MESSAGE_DISCOVERY_STATE",
                entity_type="conversation",
                reason_codes=[],
                metadata_json={
                    "state": state,
                    "external_conversation_id": item.summary.external_conversation_id,
                },
            )
        )
