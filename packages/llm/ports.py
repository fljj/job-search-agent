from typing import Protocol

from packages.job_parser.models import JobInput, ParsedJob
from packages.llm.models import (
    ConversationEvaluation,
    ConversationEvaluationRequest,
    GeneratedMessage,
    GreetingRequest,
    JobContactDecisionOutput,
    JobContactDecisionRequest,
    LlmResult,
    MessageClassification,
    MessageClassificationRequest,
    ReplyRequest,
)


class LlmProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def prompt_version(self, purpose: str) -> str: ...

    def health_check(self) -> None: ...

    def parse_job(self, request: JobInput) -> LlmResult[ParsedJob]: ...

    def decide_job_contact(
        self, request: JobContactDecisionRequest
    ) -> LlmResult[JobContactDecisionOutput]: ...

    def classify_message(
        self, request: MessageClassificationRequest
    ) -> LlmResult[MessageClassification]: ...

    def generate_greeting(self, request: GreetingRequest) -> LlmResult[GeneratedMessage]: ...

    def generate_reply(self, request: ReplyRequest) -> LlmResult[GeneratedMessage]: ...

    def evaluate_conversation(
        self, request: ConversationEvaluationRequest
    ) -> LlmResult[ConversationEvaluation]: ...
