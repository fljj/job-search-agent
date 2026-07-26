from uuid import uuid4

from packages.resume_selector.selector import ResumeCandidate, select_default_resume


def test_selects_first_available_resume_without_job_matching() -> None:
    resume = ResumeCandidate(uuid4(), "Java后端.pdf", ["Java后端"], True)
    assert select_default_resume([resume]) == resume


def test_does_not_select_unavailable_resume() -> None:
    resume = ResumeCandidate(uuid4(), "Java后端.pdf", ["Java后端"], False)
    assert select_default_resume([resume]) is None


def test_multiple_available_resumes_select_first_as_platform_default() -> None:
    resumes = [
        ResumeCandidate(uuid4(), "Java后端-A.pdf", ["Java后端"], True),
        ResumeCandidate(uuid4(), "产品.pdf", ["产品"], True),
    ]
    assert select_default_resume(resumes) == resumes[0]
