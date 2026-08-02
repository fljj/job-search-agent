import re

from packages.job_parser.models import WorkMode

_ONSITE_MARKERS = (
    "不支持远程",
    "不接受远程",
    "必须现场",
    "现场办公",
    "线下办公",
    "驻场办公",
    "需要坐班",
)
_HYBRID_MARKERS = ("混合办公", "hybrid")
_REMOTE_MARKERS = ("远程", "remote", "work from home", "wfh", "居家办公")
_NON_ONSITE_LOCATIONS = {"远程", "全国", "不限", "全球", "global"}


def infer_effective_work_mode(
    work_mode: str | WorkMode,
    *,
    title: str,
    description: str,
    location: str | None,
    infer_onsite_from_location: bool = True,
) -> WorkMode:
    """平台未标注工作模式时，使用 JD 证据和明确城市完成策略判断。"""
    current = WorkMode(work_mode)
    if current is not WorkMode.UNKNOWN:
        return current

    text = f"{title}\n{description}".casefold()
    compact_text = "".join(text.split())
    if any("".join(marker.casefold().split()) in compact_text for marker in _ONSITE_MARKERS):
        return WorkMode.ONSITE
    if any(_contains_marker(text, marker) for marker in _HYBRID_MARKERS):
        return WorkMode.HYBRID
    if any(_contains_marker(text, marker) for marker in _REMOTE_MARKERS):
        return WorkMode.REMOTE

    normalized_location = "".join((location or "").casefold().split())
    if (
        infer_onsite_from_location
        and normalized_location
        and normalized_location not in _NON_ONSITE_LOCATIONS
    ):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def _contains_marker(text: str, marker: str) -> bool:
    normalized = marker.casefold().strip()
    if normalized.isascii():
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
            text,
        ) is not None
    return "".join(normalized.split()) in "".join(text.split())
