from packages.scoring.models import RejectionReason, ScoreDetail


def build_match_reasons(details: list[ScoreDetail]) -> list[str]:
    return [detail.explanation for detail in details if detail.score > 0][:7]


def build_risk_notes(details: list[ScoreDetail], rejections: list[RejectionReason], warnings: list[str]) -> list[str]:
    notes = list(warnings)
    risk_codes = {"WORK_MODE_UNKNOWN", "SALARY_UNKNOWN", "SALARY_UNCERTAIN", "INDUSTRY_UNKNOWN", "YEARS_UNKNOWN"}
    notes.extend(detail.explanation for detail in details if detail.rule_code in risk_codes)
    notes.extend(reason.message for reason in rejections)
    return list(dict.fromkeys(notes))
