import json
from functools import lru_cache
from pathlib import Path

from packages.job_parser.models import RuleParserConfig


@lru_cache
def get_job_parser_config() -> RuleParserConfig:
    path = Path(__file__).resolve().parents[4] / "config" / "job-parser.json"
    return RuleParserConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
