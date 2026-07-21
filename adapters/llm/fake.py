from packages.job_parser.models import JobInput, ParsedJob
from packages.job_parser.rule_parser import RuleJobParser


class FakeLlmJobParser:
    """测试用适配器，不访问任何外部模型。"""

    def parse(self, job: JobInput) -> ParsedJob:
        parsed = RuleJobParser().parse(job)
        return parsed.model_copy(update={"parser_type": "FAKE_LLM", "parser_version": "fake-1.0.0"})
