from typing import Protocol

from packages.job_parser.models import JobInput, ParsedJob


class LlmJobParser(Protocol):
    def parse(self, job: JobInput) -> ParsedJob: ...
