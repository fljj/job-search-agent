from dataclasses import dataclass, field
from typing import cast

import pytest

from adapters.browser.playwright_reader import validate_local_cdp_url
from apps.api.app.core.browser_config import get_browser_selectors
from packages.browser_worker.config import PlatformSelectors
from packages.browser_worker.extractor import extract_current_page
from packages.browser_worker.models import (
    PageType,
    Platform,
    PlatformConsentType,
    SessionStatus,
)
from packages.browser_worker.ports import ElementReader


@dataclass
class FakeElement:
    texts: dict[str, str] = field(default_factory=dict)
    attributes: dict[tuple[str, str], str] = field(default_factory=dict)

    def text(self, selector: str) -> str | None:
        return self.texts.get(selector)

    def attribute(self, selector: str, name: str) -> str | None:
        return self.attributes.get((selector, name))


@dataclass
class FakePage(FakeElement):
    url: str = "https://www.zhipin.com/job_detail/abc"
    title: str = "职位详情"
    visible: set[str] = field(default_factory=set)
    element_lists: dict[str, list[FakeElement]] = field(default_factory=dict)

    def exists(self, selector: str) -> bool:
        return selector in self.visible

    def elements(self, selector: str) -> list[ElementReader]:
        return [cast(ElementReader, item) for item in self.element_lists.get(selector, [])]


def job_page(platform: Platform) -> tuple[FakePage, PlatformSelectors]:
    config = get_browser_selectors()
    selectors = config.platforms[platform.value]
    host = "www.zhipin.com" if platform is Platform.BOSS else "maimai.cn"
    page = FakePage(url=f"https://{host}/job/abc")
    page.visible = {selectors.login_marker, selectors.job_root, selectors.job_open_marker}
    page.texts = {
        selectors.job_title: "高级Java后端工程师",
        selectors.company: "示例科技",
        selectors.industry: "互联网",
        selectors.location: "北京",
        selectors.work_mode: "支持远程",
        selectors.salary: "35K-45K",
        selectors.description: "要求 Java 和 Spring Boot 经验",
    }
    page.attributes = {(selectors.job_id, "data-job-id"): "job-abc"}
    return page, selectors


@pytest.mark.parametrize("platform", [Platform.BOSS, Platform.MAIMAI])
def test_extracts_supported_platform_job_fixture(platform: Platform) -> None:
    page, selectors = job_page(platform)
    result = extract_current_page(page, platform, selectors, "v1")
    assert result.status is SessionStatus.SESSION_READY
    assert result.page_type is PageType.JOB
    assert result.job and result.job.work_mode == "REMOTE"
    assert result.job.source_status == "OPEN"
    assert result.job.external_job_id == "job-abc"


def test_closed_marker_takes_priority_over_open_action() -> None:
    page, selectors = job_page(Platform.BOSS)
    page.visible.add(selectors.job_closed_marker)
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.job and result.job.source_status == "CLOSED"


def test_normalizes_boss_company_accessibility_prefix() -> None:
    page, selectors = job_page(Platform.BOSS)
    page.texts[selectors.company] = "公司名称示例科技"
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.job and result.job.company_name == "示例科技"


def test_boss_job_with_location_keeps_unknown_when_mode_is_absent() -> None:
    page, selectors = job_page(Platform.BOSS)
    page.texts.pop(selectors.work_mode)

    result = extract_current_page(page, Platform.BOSS, selectors, "v1")

    assert result.job and result.job.work_mode == "UNKNOWN"


def test_boss_job_title_can_identify_remote_work_mode() -> None:
    page, selectors = job_page(Platform.BOSS)
    page.texts.pop(selectors.work_mode)
    page.texts[selectors.job_title] = "Java 开发工程师（Web3 居家办公）"

    result = extract_current_page(page, Platform.BOSS, selectors, "v1")

    assert result.job and result.job.work_mode == "REMOTE"


def test_preserves_boss_recruiting_agency_company_prefix_as_scoring_evidence() -> None:
    page, selectors = job_page(Platform.BOSS)
    page.texts[selectors.company] = "代招公司：上海某大型证券公司"
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.job and result.job.company_name == "代招公司：上海某大型证券公司"


def test_extracts_conversation_messages() -> None:
    config = get_browser_selectors()
    selectors = config.platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/chat")
    page.visible = {selectors.login_marker, selectors.conversation_root}
    page.texts = {selectors.recruiter: "张HR"}
    page.attributes = {(selectors.conversation_id, selectors.conversation_id_attribute): "chat-1"}
    page.element_lists = {
        selectors.message_items: [
            FakeElement(
                texts={selectors.message_content: "请发一份简历"},
                attributes={("", selectors.message_id_attribute): "message-1"},
            )
        ]
    }
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.page_type is PageType.CONVERSATION
    assert (
        result.conversation and result.conversation.messages[0].external_message_id == "message-1"
    )


def test_extracts_only_pending_exact_platform_consent_cards() -> None:
    selectors = get_browser_selectors().platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/chat")
    page.visible = {selectors.login_marker, selectors.conversation_root}
    page.texts = {selectors.recruiter: "张先生"}
    page.attributes = {(selectors.conversation_id, selectors.conversation_id_attribute): "chat-1"}
    page.element_lists = {
        selectors.consent_cards: [
            FakeElement(
                texts={
                    selectors.consent_card_title: ("我想要和您交换联系方式，您是否同意"),
                    selectors.consent_card_buttons: "同意",
                },
                attributes={(selectors.consent_card_buttons, "class"): "card-btn"},
            ),
            FakeElement(
                texts={
                    selectors.consent_card_title: ("我想要一份您的附件简历，您是否同意"),
                    selectors.consent_card_buttons: "同意",
                },
                attributes={(selectors.consent_card_buttons, "class"): "card-btn disabled"},
            ),
            FakeElement(
                texts={
                    selectors.consent_card_title: "这不是受支持的平台动作",
                    selectors.consent_card_buttons: "同意",
                }
            ),
        ],
        selectors.location_consent_cards: [
            FakeElement(
                texts={
                    selectors.location_consent_title: "您是否接受此工作地点?",
                    selectors.location_consent_button: "可以接受",
                },
                attributes={
                    (
                        selectors.location_consent_detail,
                        "aria-label",
                    ): "世纪开元文化创意产业园（济南历城区）",
                    (
                        selectors.location_consent_button,
                        "class",
                    ): "btn-v2 btn-light-v2",
                },
            )
        ],
    }

    result = extract_current_page(page, Platform.BOSS, selectors, "v1")

    assert result.conversation is not None
    assert len(result.conversation.platform_consents) == 3
    assert result.conversation.platform_consents[0].consent_type is (PlatformConsentType.CONTACT)
    assert result.conversation.platform_consents[0].pending is True
    assert result.conversation.platform_consents[1].consent_type is (PlatformConsentType.RESUME)
    assert result.conversation.platform_consents[1].pending is False
    assert result.conversation.platform_consents[2].consent_type is (
        PlatformConsentType.LOCATION
    )
    assert result.conversation.platform_consents[2].detail == (
        "世纪开元文化创意产业园（济南历城区）"
    )
    assert result.conversation.platform_consents[2].pending is True


def test_extracts_real_boss_conversation_list_id_from_d_c() -> None:
    selectors = get_browser_selectors().platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/chat")
    page.visible = {selectors.login_marker, selectors.conversation_list_root}
    page.element_lists = {
        selectors.conversation_list_items: [
            FakeElement(
                texts={selectors.conversation_list_item_recruiter: "李招聘"},
                attributes={("", "d-c"): "boss-chat-1"},
            )
        ]
    }

    result = extract_current_page(page, Platform.BOSS, selectors, "v1")

    assert result.status is SessionStatus.SESSION_READY
    assert result.page_type is PageType.CONVERSATION_LIST
    assert result.conversations[0].external_conversation_id == "boss-chat-1"


def test_maimai_conversation_list_requires_stable_last_message_identity() -> None:
    selectors = get_browser_selectors().platforms["MAIMAI"]
    page = FakePage(url="https://maimai.cn/web/feed_im")
    page.visible = {selectors.login_marker, selectors.conversation_list_root}
    page.element_lists = {
        selectors.conversation_list_items: [
            FakeElement(
                texts={selectors.conversation_list_item_recruiter: "李招聘"},
                attributes={
                    ("", selectors.conversation_list_item_id_attribute): (
                        '{"mid":"maimai-chat-1"}'
                    ),
                },
            )
        ]
    }

    result = extract_current_page(page, Platform.MAIMAI, selectors, "v1")

    assert result.status is SessionStatus.SESSION_PAGE_CHANGED
    assert result.reason_codes == ["NO_RECOGNIZABLE_CONVERSATION_LIST_ITEM"]
    assert result.conversations == []


def test_extracts_real_boss_conversation_dom_shape() -> None:
    selectors = get_browser_selectors().platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/chat")
    page.visible = {selectors.login_marker, selectors.conversation_root}
    page.texts = {
        selectors.recruiter: "李剑",
        selectors.conversation_job_title: "java开发工程师（代招职位）",
    }
    page.attributes = {(selectors.conversation_id, selectors.conversation_id_attribute): "62001"}
    page.element_lists = {
        selectors.message_items: [
            FakeElement(
                texts={selectors.message_content: "你好，最近看新机会吗"},
                attributes={
                    ("", selectors.message_id_attribute): "367232427933707",
                    ("", "class"): "message-item item-friend",
                },
            ),
            FakeElement(
                texts={selectors.message_content: "您好，可以具体聊聊"},
                attributes={
                    ("", selectors.message_id_attribute): "367232427933708",
                    ("", "class"): "message-item item-myself",
                },
            ),
        ]
    }

    result = extract_current_page(page, Platform.BOSS, selectors, "v1")

    assert result.status is SessionStatus.SESSION_READY
    assert result.conversation
    assert result.conversation.external_conversation_id == "62001"
    assert result.conversation.job_title == "java开发工程师（代招职位）"
    assert result.conversation.messages[0].direction.value == "INBOUND"
    assert result.conversation.messages[1].direction.value == "OUTBOUND"


def test_job_list_pauses_when_no_item_is_recognizable() -> None:
    config = get_browser_selectors()
    selectors = config.platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/job")
    page.visible = {selectors.login_marker, selectors.job_list_root}
    page.element_lists = {
        selectors.job_list_items: [
            FakeElement(
                texts={selectors.job_list_item_title: "缺少公司字段"},
                attributes={
                    ("", selectors.job_list_item_id_attribute): "job-1",
                },
            )
        ]
    }
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.status is SessionStatus.SESSION_PAGE_CHANGED
    assert result.reason_codes == ["NO_RECOGNIZABLE_JOB_LIST_ITEM"]
    assert result.jobs == []


def test_job_list_uses_detail_url_when_real_card_has_no_job_id_attribute() -> None:
    config = get_browser_selectors()
    selectors = config.platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/jobs")
    page.visible = {selectors.login_marker, selectors.job_list_root}
    page.element_lists = {
        selectors.job_list_items: [
            FakeElement(
                texts={
                    selectors.job_list_item_title: "Java 后端开发",
                    selectors.job_list_item_company: "示例科技",
                },
                attributes={
                    (
                        selectors.job_list_item_link,
                        "href",
                    ): "/job_detail/e29439525cc4e0810nd92d64FlBS.html",
                },
            )
        ]
    }

    result = extract_current_page(page, Platform.BOSS, selectors, "v1")

    assert result.status is SessionStatus.SESSION_READY
    assert result.jobs[0].external_job_id == "e29439525cc4e0810nd92d64FlBS"


def test_invalid_message_time_skips_only_invalid_message() -> None:
    config = get_browser_selectors()
    selectors = config.platforms["BOSS"]
    page = FakePage(url="https://www.zhipin.com/web/geek/chat")
    page.visible = {selectors.login_marker, selectors.conversation_root}
    page.texts = {selectors.recruiter: "张HR"}
    page.attributes = {(selectors.conversation_id, selectors.conversation_id_attribute): "chat-1"}
    page.element_lists = {
        selectors.message_items: [
            FakeElement(
                texts={selectors.message_content: "您好"},
                attributes={
                    ("", selectors.message_id_attribute): "message-1",
                    ("", selectors.message_time_attribute): "not-a-time",
                },
            )
        ]
    }
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.status is SessionStatus.SESSION_READY
    assert result.reason_codes == ["INVALID_MESSAGE_TIME"]
    assert result.conversation and result.conversation.messages == []


@pytest.mark.parametrize(
    ("visible_extra", "expected", "reason"),
    [
        ("verification", SessionStatus.SESSION_AUTH_REQUIRED, "VERIFICATION_REQUIRED"),
        ("no_login", SessionStatus.SESSION_AUTH_REQUIRED, "LOGIN_REQUIRED"),
        ("unknown_page", SessionStatus.SESSION_PAGE_CHANGED, "SUPPORTED_PAGE_ROOT_NOT_FOUND"),
    ],
)
def test_stops_on_unsafe_or_changed_page(
    visible_extra: str, expected: SessionStatus, reason: str
) -> None:
    config = get_browser_selectors()
    selectors = config.platforms["BOSS"]
    page = FakePage()
    if visible_extra == "verification":
        page.visible = {selectors.login_marker, selectors.verification_marker}
    elif visible_extra == "unknown_page":
        page.visible = {selectors.login_marker}
    result = extract_current_page(page, Platform.BOSS, selectors, "v1")
    assert result.status is expected
    assert result.reason_codes == [reason]
    assert result.job is None and result.conversation is None


def test_stops_when_expected_target_does_not_match() -> None:
    page, selectors = job_page(Platform.BOSS)
    result = extract_current_page(
        page, Platform.BOSS, selectors, "v1", expected_company="另一家公司"
    )
    assert result.status is SessionStatus.SESSION_TARGET_MISMATCH
    assert result.reason_codes == ["JOB_TARGET_MISMATCH"]


def test_cdp_endpoint_must_be_local_and_without_credentials() -> None:
    validate_local_cdp_url("http://127.0.0.1:9222")
    with pytest.raises(ValueError):
        validate_local_cdp_url("https://example.com:9222")
    with pytest.raises(ValueError):
        validate_local_cdp_url("http://user:secret@localhost:9222")
