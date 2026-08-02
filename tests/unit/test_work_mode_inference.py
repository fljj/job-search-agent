import pytest

from packages.job_matching.work_mode import infer_effective_work_mode
from packages.job_parser.models import WorkMode


@pytest.mark.parametrize(
    ("title", "description", "location", "expected"),
    [
        ("后端开发专家（国际化）", "负责 Java 服务端研发", "上海", WorkMode.ONSITE),
        ("Java 开发", "支持 Remote 工作", "上海", WorkMode.REMOTE),
        ("Java 开发", "采用混合办公模式", "上海", WorkMode.HYBRID),
        ("Java 开发", "不支持远程，需现场办公", "上海", WorkMode.ONSITE),
        ("Java 开发", "工作模式可协商", None, WorkMode.UNKNOWN),
    ],
)
def test_infer_effective_work_mode(
    title: str,
    description: str,
    location: str | None,
    expected: WorkMode,
) -> None:
    assert infer_effective_work_mode(
        WorkMode.UNKNOWN,
        title=title,
        description=description,
        location=location,
    ) is expected


def test_explicit_platform_mode_is_preserved() -> None:
    assert infer_effective_work_mode(
        WorkMode.REMOTE,
        title="Java 开发",
        description="办公地址上海",
        location="上海",
    ) is WorkMode.REMOTE
