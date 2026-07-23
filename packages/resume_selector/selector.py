from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ResumeCandidate:
    id: UUID
    attachment_name: str
    target_directions: list[str]
    is_available: bool


def select_resume(resumes: list[ResumeCandidate], job_title: str) -> ResumeCandidate | None:
    lowered = job_title.lower()
    matches = [
        resume
        for resume in resumes
        if resume.is_available
        and any(direction.lower() in lowered for direction in resume.target_directions)
    ]
    return matches[0] if len(matches) == 1 else None
