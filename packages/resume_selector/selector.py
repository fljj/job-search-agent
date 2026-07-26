from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ResumeCandidate:
    id: UUID
    attachment_name: str
    target_directions: list[str]
    is_available: bool


def select_default_resume(
    resumes: list[ResumeCandidate],
) -> ResumeCandidate | None:
    """选择平台已登记的第一份可用简历，不按职位标题二次匹配。"""
    return next((resume for resume in resumes if resume.is_available), None)
