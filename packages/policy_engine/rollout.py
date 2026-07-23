from enum import IntEnum


class RolloutLevel(IntEnum):
    MESSAGE_READ_ONLY = 1
    JOB_READ_ONLY = 2
    LIMITED_REPLY = 3
    LIMITED_GREETING = 4
    RESUME_ENABLED = 5
    FORMAL_LIMITS = 6


LEVEL_NAMES = {
    RolloutLevel.MESSAGE_READ_ONLY: "只读消息",
    RolloutLevel.JOB_READ_ONLY: "只读职位并评分",
    RolloutLevel.LIMITED_REPLY: "限量普通回复",
    RolloutLevel.LIMITED_GREETING: "限量主动招呼",
    RolloutLevel.RESUME_ENABLED: "按索要发送简历",
    RolloutLevel.FORMAL_LIMITS: "正式配置限额",
}


def allows_job_scan(level: int) -> bool:
    return level >= RolloutLevel.JOB_READ_ONLY


def action_limit(
    level: int,
    action_type: str,
    *,
    reply_daily_limit: int,
    greeting_daily_limit: int,
    formal_daily_limit: int,
) -> int:
    if action_type in {"REPLY", "LOW_SCORE_DECLINE", "MISMATCH_DECLINE"}:
        if level < RolloutLevel.LIMITED_REPLY:
            return 0
        return formal_daily_limit if level >= RolloutLevel.FORMAL_LIMITS else reply_daily_limit
    if action_type == "GREETING":
        if level < RolloutLevel.LIMITED_GREETING:
            return 0
        return formal_daily_limit if level >= RolloutLevel.FORMAL_LIMITS else greeting_daily_limit
    if action_type == "RESUME":
        if level < RolloutLevel.RESUME_ENABLED:
            return 0
        return formal_daily_limit
    if action_type in {
        "PLATFORM_RECOMMENDATION_ACCEPT",
        "PLATFORM_RECOMMENDATION_REJECT",
    }:
        if level < RolloutLevel.RESUME_ENABLED:
            return 0
        return formal_daily_limit
    return 0
