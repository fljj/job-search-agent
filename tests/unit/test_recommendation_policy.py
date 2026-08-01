import pytest

from adapters.browser.maimai_recommendations import (
    _has_recommendation_request,
    _is_system_recommendation_preview,
)
from apps.api.app.core.recommendation_config import get_recommendation_rules
from packages.policy_engine.recommendation import (
    RecommendationDecision,
    decide_recommendation,
)


@pytest.mark.parametrize(
    "text",
    [
        "Java 后端开发",
        "AI 大模型研发",
        "直播运营",
        "技术架构师",
        "Vibe Coding 工程师",
    ],
)
def test_related_recommendation_is_accepted(text: str) -> None:
    decision, reasons = decide_recommendation(
        recruiter="招聘顾问",
        company="示例公司",
        job_title=text,
        card_text=text,
        rules=get_recommendation_rules(),
    )
    assert decision is RecommendationDecision.ACCEPT_AND_SEND_PROFILE
    assert reasons == ["RELATED_DIRECTION_MATCHED"]


@pytest.mark.parametrize(
    "text",
    ["保险销售顾问", "保险代理人", "保险客户经理"],
)
def test_rejected_direction_takes_precedence_over_related_word(text: str) -> None:
    decision, reasons = decide_recommendation(
        recruiter="招聘顾问",
        company="示例公司",
        job_title=text,
        card_text=f"{text} 数据运营",
        rules=get_recommendation_rules(),
    )
    assert decision is RecommendationDecision.REJECT_RECOMMENDATION
    assert reasons == ["REJECTED_DIRECTION_MATCHED"]


def test_irrelevant_recommendation_is_rejected() -> None:
    decision, reasons = decide_recommendation(
        recruiter="招聘顾问",
        company="示例公司",
        job_title="商场保洁",
        card_text="负责商场公共区域清洁",
        rules=get_recommendation_rules(),
    )
    assert decision is RecommendationDecision.REJECT_RECOMMENDATION
    assert reasons == ["NO_RELATED_DIRECTION_EVIDENCE"]


def test_company_blacklist_is_rejected() -> None:
    decision, reasons = decide_recommendation(
        recruiter="招聘顾问",
        company="黑名单公司",
        job_title="Java 开发",
        card_text="Java 开发",
        rules=get_recommendation_rules(),
        blacklisted_companies=["黑名单公司"],
    )
    assert decision is RecommendationDecision.REJECT_RECOMMENDATION
    assert reasons == ["COMPANY_BLACKLISTED"]


def test_official_account_is_denied() -> None:
    decision, reasons = decide_recommendation(
        recruiter="脉脉官方服务",
        company="脉脉",
        job_title="资料审核通知",
        card_text="资料审核通知",
        rules=get_recommendation_rules(),
    )
    assert decision is RecommendationDecision.DENY
    assert reasons == ["OFFICIAL_ACCOUNT"]


def test_headhunter_inbound_is_not_rejected_by_identity() -> None:
    decision, _ = decide_recommendation(
        recruiter="猎头顾问",
        company="猎头公司",
        job_title="Java 后端开发",
        card_text="系统推荐 Java 后端开发",
        rules=get_recommendation_rules(),
    )
    assert decision is RecommendationDecision.ACCEPT_AND_SEND_PROFILE


def test_real_maimai_recommendation_preview_is_verified_in_two_steps() -> None:
    rules = get_recommendation_rules()

    assert _is_system_recommendation_preview("[系统推荐][消息卡片]", rules)
    assert not _is_system_recommendation_preview("可以要一份你的简历吗？", rules)
    assert _has_recommendation_request(
        "我们正在招 Java 后端人才，可以要一份你的简历吗？",
        rules,
    )
    assert not _has_recommendation_request("普通招聘沟通", rules)
