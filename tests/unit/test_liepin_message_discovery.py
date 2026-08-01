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
    monkeypatch.setattr(
        MessageDiscoveryAdapter,
        "scan",
        lambda *_args, **_kwargs: _batch(),
    )

    adapter.scan("http://127.0.0.1:9222")

    assert adapter.home_ready_for_job_discovery is False
    restore.assert_not_called()


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
