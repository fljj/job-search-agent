from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

from apps.api.app.services.llm_service import record_llm_invocation
from packages.llm.models import LlmCallMetadata


def test_record_llm_invocation_stores_metadata_only() -> None:
    session = MagicMock(spec=Session)
    invocation = record_llm_invocation(
        session,
        user_id=uuid4(),
        purpose="MESSAGE_CLASSIFY",
        input_hash="a" * 64,
        status="SUCCEEDED",
        metadata=LlmCallMetadata(
            provider="QWEN",
            model="qwen-plus",
            prompt_version="message-classify-v1",
            response_id="response-1",
            latency_ms=80,
            input_tokens=10,
            output_tokens=5,
        ),
    )

    session.add.assert_called_once_with(invocation)
    session.flush.assert_called_once_with()
    assert invocation.provider == "QWEN"
    assert not hasattr(invocation, "api_key")
    assert not hasattr(invocation, "prompt")
