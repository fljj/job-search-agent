import pytest

from packages.job_parser.source_url import normalize_job_source_url


def test_normalizes_job_source_url_and_removes_tracking_parameters() -> None:
    assert normalize_job_source_url(
        "BOSS",
        "http://www.zhipin.com/job_detail/job-1.html?utm_source=test&ka=search#detail",
    ) == "https://www.zhipin.com/job_detail/job-1.html?ka=search"


def test_rejects_job_source_url_from_another_platform() -> None:
    with pytest.raises(ValueError, match="职位来源链接"):
        normalize_job_source_url("BOSS", "https://example.com/job/1")
