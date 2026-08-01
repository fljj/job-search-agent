import hashlib
import json
from functools import lru_cache
from pathlib import Path

from packages.browser_worker.config import (
    BrowserSelectorsConfig,
    PlatformSelectorDocument,
)
from packages.browser_worker.models import Platform


def load_browser_selectors(directory: Path) -> BrowserSelectorsConfig:
    documents: dict[str, PlatformSelectorDocument] = {}
    for path in sorted(directory.glob("*.json")):
        document = PlatformSelectorDocument.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        if document.platform in documents:
            raise ValueError(f"重复的浏览器平台配置: {document.platform}")
        expected_name = document.platform.lower()
        if path.stem != expected_name:
            raise ValueError(
                f"浏览器平台配置文件名不匹配: {path.name} 应为 {expected_name}.json"
            )
        documents[document.platform] = document
    required = {platform.value for platform in Platform}
    missing = required - documents.keys()
    unexpected = documents.keys() - required
    if missing:
        raise ValueError(f"缺少浏览器平台配置: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"未知浏览器平台配置: {', '.join(sorted(unexpected))}")
    version_source = "|".join(
        f"{platform}:{documents[platform].version}" for platform in sorted(documents)
    )
    bundle_version = f"bundle-{hashlib.sha256(version_source.encode()).hexdigest()[:12]}"
    return BrowserSelectorsConfig(
        version=bundle_version,
        platforms={
            platform: document.to_runtime()
            for platform, document in documents.items()
        },
    )


@lru_cache
def get_browser_selectors() -> BrowserSelectorsConfig:
    directory = Path(__file__).resolve().parents[4] / "config" / "browser-selectors"
    return load_browser_selectors(directory)
