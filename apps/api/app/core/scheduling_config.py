import json
from functools import lru_cache
from pathlib import Path

from packages.scheduling.models import SchedulingConfig


@lru_cache
def get_scheduling_config() -> SchedulingConfig:
    path = Path(__file__).resolve().parents[4] / "config" / "scheduling-policy.json"
    return SchedulingConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
