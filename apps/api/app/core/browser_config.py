import json
from functools import lru_cache
from pathlib import Path

from packages.browser_worker.config import BrowserSelectorsConfig


@lru_cache
def get_browser_selectors() -> BrowserSelectorsConfig:
    path = Path(__file__).resolve().parents[4] / "config" / "browser-selectors.json"
    return BrowserSelectorsConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
