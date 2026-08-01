from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from adapters.browser.liepin_message_discovery import LiepinMessageDiscoveryAdapter
from adapters.browser.message_discovery import MessageDiscoveryAdapter, MessageDiscoveryBatch
from apps.api.app.core.browser_config import get_browser_selectors
from packages.browser_worker.models import (
    PageType,
    Platform,
    ReadResult,
    SessionStatus,
)


def _batch() -> MessageDiscoveryBatch:
    return MessageDiscoveryBatch(
        platform=Platform.LIEPIN,
        partition="ALL",
        scroll_position=0,
        scanned_at=datetime.now(UTC),
    )


def test_agent_opened_drawer_is_restored_after_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    result = _batch()
    restore = MagicMock()
    monkeypatch.setattr(adapter, "_find_home_target", lambda _url: "ws://home")
    monkeypatch.setattr(adapter, "_ensure_drawer_open", lambda _target: True)
    monkeypatch.setattr(adapter, "_restore_home", restore)
    monkeypatch.setattr(
        MessageDiscoveryAdapter,
        "scan",
        lambda *_args, **_kwargs: result,
    )

    assert adapter.scan("http://127.0.0.1:9222") is result
    assert adapter.home_ready_for_job_discovery is True
    restore.assert_called_once_with("ws://home")


def test_user_opened_drawer_is_not_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    restore = MagicMock()
    monkeypatch.setattr(adapter, "_find_home_target", lambda _url: "ws://home")
    monkeypatch.setattr(adapter, "_ensure_drawer_open", lambda _target: False)
    monkeypatch.setattr(adapter, "_restore_home", restore)
    prepare = MagicMock(return_value=True)
    monkeypatch.setattr(adapter, "_prepare_user_drawer_for_job_discovery", prepare)
    monkeypatch.setattr(
        MessageDiscoveryAdapter,
        "scan",
        lambda *_args, **_kwargs: _batch(),
    )

    adapter.hold_drawer_for_actions()
    adapter.scan("http://127.0.0.1:9222")
    adapter.finish_actions()

    assert adapter.home_ready_for_job_discovery is True
    restore.assert_not_called()
    prepare.assert_called_once_with("ws://home")


def test_user_opened_drawer_closes_conversation_but_keeps_job_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    conversation_checks = iter([True, False])

    def exists(selector: str) -> bool:
        if selector == adapter.selectors.conversation_root:
            return next(conversation_checks)
        return selector == adapter.selectors.job_list_root

    page.exists.side_effect = exists
    page._evaluate.return_value = True
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.extract_current_page",
        lambda *_args, **_kwargs: ReadResult(
            platform=Platform.LIEPIN,
            status=SessionStatus.SESSION_READY,
            page_type=PageType.CONVERSATION,
            page_url="https://c.liepin.com/",
            page_title="猎聘会话",
            content_hash="d" * 64,
            selector_version="fixture",
        ),
    )

    assert adapter._prepare_user_drawer_for_job_discovery("ws://home") is True
    expression = page._evaluate.call_args.args[0]
    assert adapter.selectors.conversation_dialog_close_button in expression
    assert adapter.selectors.conversation_drawer_close_button not in expression


def test_agent_drawer_is_held_until_l4_actions_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    restore = MagicMock()
    monkeypatch.setattr(adapter, "_find_home_target", lambda _url: "ws://home")
    monkeypatch.setattr(adapter, "_ensure_drawer_open", lambda _target: True)
    monkeypatch.setattr(adapter, "_restore_home", restore)
    monkeypatch.setattr(
        MessageDiscoveryAdapter,
        "scan",
        lambda *_args, **_kwargs: _batch(),
    )

    adapter.hold_drawer_for_actions()
    adapter.scan("http://127.0.0.1:9222")

    restore.assert_not_called()
    assert adapter.home_ready_for_job_discovery is False

    adapter.finish_actions()

    restore.assert_called_once_with("ws://home")
    assert adapter.home_ready_for_job_discovery is True


def test_agent_opened_drawer_is_restored_when_scan_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    restore = MagicMock()
    monkeypatch.setattr(adapter, "_find_home_target", lambda _url: "ws://home")
    monkeypatch.setattr(adapter, "_ensure_drawer_open", lambda _target: True)
    monkeypatch.setattr(adapter, "_restore_home", restore)
    monkeypatch.setattr(
        MessageDiscoveryAdapter,
        "scan",
        MagicMock(side_effect=ValueError("详情读取失败")),
    )

    with pytest.raises(ValueError, match="详情读取失败"):
        adapter.scan("http://127.0.0.1:9222")

    restore.assert_called_once_with("ws://home")


def test_pending_user_input_prevents_agent_from_closing_drawer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.extract_current_page",
        lambda *_args, **_kwargs: ReadResult(
            platform=Platform.LIEPIN,
            status=SessionStatus.SESSION_PAUSED,
            page_url="https://c.liepin.com/",
            page_title="猎聘首页",
            content_hash="a" * 64,
            selector_version="fixture",
            reason_codes=["PENDING_USER_INPUT"],
        ),
    )

    with pytest.raises(ValueError, match="PENDING_USER_INPUT"):
        adapter._restore_home("ws://home")

    page._evaluate.assert_not_called()


def test_open_drawer_requires_safe_job_list_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    page.exists.return_value = False
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.extract_current_page",
        lambda *_args, **_kwargs: ReadResult(
            platform=Platform.LIEPIN,
            status=SessionStatus.SESSION_READY,
            page_type=PageType.CONVERSATION,
            page_url="https://c.liepin.com/",
            page_title="猎聘首页",
            content_hash="b" * 64,
            selector_version="fixture",
        ),
    )

    with pytest.raises(ValueError, match="不能安全打开消息抽屉"):
        adapter._ensure_drawer_open("ws://home")

    page._evaluate.assert_not_called()


def test_liepin_message_metadata_is_normalized_before_reading() -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    page = MagicMock()

    adapter._prepare_conversation_detail(page)

    expression = page._evaluate.call_args.args[0]
    assert "__reactInternalInstance" in expression
    assert "__reactFiber" in expression
    assert "message.msgId" in expression
    assert "message.msgTime" in expression
    assert "data-message-id" in expression
    assert "data-sent-at" in expression
    assert "data-direction" in expression


def test_restore_closes_active_conversation_before_drawer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LiepinMessageDiscoveryAdapter(get_browser_selectors())
    page = MagicMock()
    page.__enter__.return_value = page
    page.__exit__.return_value = False
    conversation_checks = iter([True, False])

    def exists(selector: str) -> bool:
        if selector == adapter.selectors.conversation_root:
            return next(conversation_checks)
        if selector == adapter.selectors.conversation_list_root:
            return False
        return selector == adapter.selectors.job_list_root

    page.exists.side_effect = exists
    page._evaluate.return_value = True
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.RawCdpPageReader",
        lambda _target: page,
    )
    monkeypatch.setattr(
        "adapters.browser.liepin_message_discovery.extract_current_page",
        lambda *_args, **_kwargs: ReadResult(
            platform=Platform.LIEPIN,
            status=SessionStatus.SESSION_READY,
            page_type=PageType.CONVERSATION,
            page_url="https://c.liepin.com/",
            page_title="猎聘会话",
            content_hash="c" * 64,
            selector_version="fixture",
        ),
    )

    adapter._restore_home("ws://home")

    expressions = [call.args[0] for call in page._evaluate.call_args_list]
    assert len(expressions) == 2
    assert adapter.selectors.conversation_dialog_close_button in expressions[0]
    assert adapter.selectors.conversation_drawer_close_button in expressions[1]
