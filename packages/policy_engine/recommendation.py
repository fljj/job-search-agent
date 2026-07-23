from enum import StrEnum

from pydantic import BaseModel, Field


class RecommendationDecision(StrEnum):
    ACCEPT_AND_SEND_PROFILE = "ACCEPT_AND_SEND_PROFILE"
    REJECT_RECOMMENDATION = "REJECT_RECOMMENDATION"
    DENY = "DENY"


class RecommendationRules(BaseModel):
    accepted_keywords: list[str] = Field(min_length=1)
    rejected_keywords: list[str] = Field(min_length=1)
    official_accounts: list[str] = Field(min_length=1)
    recommendation_markers: list[str] = Field(min_length=1)
    accept_success_markers: list[str] = Field(min_length=1)
    reject_success_markers: list[str] = Field(min_length=1)


def decide_recommendation(
    *,
    recruiter: str,
    company: str,
    job_title: str,
    card_text: str,
    rules: RecommendationRules,
    blacklisted_companies: list[str] | None = None,
) -> tuple[RecommendationDecision, list[str]]:
    """按受控词表判断平台推荐，不生成正式职位分数。"""
    if recruiter.strip() in rules.official_accounts:
        return RecommendationDecision.DENY, ["OFFICIAL_ACCOUNT"]
    normalized = f"{company} {job_title} {card_text}".lower()
    if any(name.lower() in normalized for name in blacklisted_companies or []):
        return RecommendationDecision.REJECT_RECOMMENDATION, ["COMPANY_BLACKLISTED"]
    rejected = [word for word in rules.rejected_keywords if word.lower() in normalized]
    if rejected:
        return RecommendationDecision.REJECT_RECOMMENDATION, [
            "REJECTED_DIRECTION_MATCHED"
        ]
    accepted = [word for word in rules.accepted_keywords if word.lower() in normalized]
    if accepted:
        return RecommendationDecision.ACCEPT_AND_SEND_PROFILE, [
            "RELATED_DIRECTION_MATCHED"
        ]
    return RecommendationDecision.REJECT_RECOMMENDATION, [
        "NO_RELATED_DIRECTION_EVIDENCE"
    ]
