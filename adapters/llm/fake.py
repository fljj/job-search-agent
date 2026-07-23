from decimal import Decimal

from packages.conversation_agent.intents import classify_intents
from packages.job_parser.models import JobInput, ParsedJob
from packages.job_parser.rule_parser import RuleJobParser
from packages.llm.models import (
    ConversationEvaluation,
    ConversationEvaluationRequest,
    GeneratedMessage,
    GreetingRequest,
    JobScoreOutput,
    LlmCallMetadata,
    LlmResult,
    MessageClassification,
    MessageClassificationRequest,
    ReplyRequest,
    ScoreDimension,
)
from packages.scoring.engine import score_job
from packages.scoring.models import ScoringContext


class FakeLlmJobParser:
    """测试用适配器，不访问任何外部模型。"""

    def parse(self, job: JobInput) -> ParsedJob:
        parsed = RuleJobParser().parse(job)
        return parsed.model_copy(update={"parser_type": "FAKE_LLM", "parser_version": "fake-1.0.0"})


class FakeLlmProvider:
    """完全离线的确定性测试替身。"""

    def parse_job(self, request: JobInput) -> LlmResult[ParsedJob]:
        return self._result(FakeLlmJobParser().parse(request), "job-parse-fake-v1")

    def score_job(self, request: ScoringContext) -> LlmResult[JobScoreOutput]:
        legacy = score_job(request)
        dimensions = [
            ScoreDimension(
                dimension=detail.dimension,
                score=detail.score,
                max_score=detail.max_score,
                reason=detail.explanation,
                evidence_refs=[],
            )
            for detail in legacy.details
        ]
        return self._result(
            JobScoreOutput(
                dimensions=dimensions,
                total_score=legacy.total_score,
                match_reasons=legacy.match_reasons,
                risk_notes=legacy.risk_notes,
                recommends_proactive_contact=legacy.total_score >= 80,
                contact_reason="FAKE_PROVIDER_TEST_RESULT",
            ),
            "job-score-fake-v1",
        )

    def classify_message(
        self, request: MessageClassificationRequest
    ) -> LlmResult[MessageClassification]:
        intents = classify_intents(request.message)
        return self._result(
            MessageClassification(
                intents=intents,
                confidence=Decimal("1"),
            ),
            "message-classify-fake-v1",
        )

    def generate_greeting(self, request: GreetingRequest) -> LlmResult[GeneratedMessage]:
        return self._result(
            GeneratedMessage(
                content=f"您好，我对贵司的{request.job_title}职位很感兴趣。",
                fact_ids=[fact.id for fact in request.facts],
                confidence=Decimal("1"),
            ),
            "greeting-fake-v1",
        )

    def generate_reply(self, request: ReplyRequest) -> LlmResult[GeneratedMessage]:
        return self._result(
            GeneratedMessage(
                content="感谢您的消息，我会结合已确认的信息回复您。",
                fact_ids=[fact.id for fact in request.facts],
                confidence=Decimal("1"),
            ),
            "reply-fake-v1",
        )

    def evaluate_conversation(
        self, request: ConversationEvaluationRequest
    ) -> LlmResult[ConversationEvaluation]:
        matches = [
            index
            for index, message in enumerate(request.messages)
            if "简历" in message or "合适" in message
        ]
        return self._result(
            ConversationEvaluation(
                resume_requested=any("简历" in request.messages[index] for index in matches),
                positive_feedback=any("合适" in request.messages[index] for index in matches),
                evidence_message_indexes=matches,
                confidence=Decimal("1"),
            ),
            "conversation-evaluate-fake-v1",
        )

    @staticmethod
    def _result[T](data: T, prompt_version: str) -> LlmResult[T]:
        return LlmResult[T](
            data=data,
            metadata=LlmCallMetadata(
                provider="FAKE",
                model="fake",
                prompt_version=prompt_version,
                latency_ms=0,
            ),
        )
