from uuid import uuid4

from packages.resume_selector.selector import ResumeCandidate, select_resume


def test_selects_available_matching_resume() -> None:
    resume = ResumeCandidate(uuid4(), "Java后端.pdf", ["Java后端"], True)
    assert select_resume([resume], "高级Java后端工程师") == resume


def test_does_not_select_unavailable_or_unmatched_resume() -> None:
    resume = ResumeCandidate(uuid4(), "Java后端.pdf", ["Java后端"], False)
    assert select_resume([resume], "Java后端") is None
