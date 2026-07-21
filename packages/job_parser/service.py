from collections.abc import Callable

from packages.job_parser.models import JobInput, ParsedJob
from packages.job_parser.rule_parser import RuleJobParser


class JobParserService:
    def __init__(self, fake_llm_parser: Callable[[JobInput], ParsedJob] | None = None) -> None:
        self.rule_parser = RuleJobParser()
        self.fake_llm_parser = fake_llm_parser

    def parse(self, job: JobInput, mode: str = "RULE") -> ParsedJob:
        if mode == "RULE":
            return self.rule_parser.parse(job)
        if mode in {"FAKE_LLM", "HYBRID_TEST"} and self.fake_llm_parser:
            return self.fake_llm_parser(job)
        raise ValueError(f"不支持的解析模式: {mode}")
