from uuid import UUID

from sqlalchemy.orm import Session

from apps.api.app.models.entities import LlmInvocation
from packages.llm.models import LlmCallMetadata


def record_llm_invocation(
    session: Session,
    *,
    user_id: UUID,
    purpose: str,
    input_hash: str,
    status: str,
    metadata: LlmCallMetadata,
    failure_code: str | None = None,
) -> LlmInvocation:
    """仅保存复现调用所需元数据，不保存密钥或完整提示词。"""
    invocation = LlmInvocation(
        user_id=user_id,
        purpose=purpose,
        provider=metadata.provider,
        model=metadata.model,
        prompt_version=metadata.prompt_version,
        input_hash=input_hash,
        status=status,
        provider_response_id=metadata.response_id,
        latency_ms=metadata.latency_ms,
        input_tokens=metadata.input_tokens,
        output_tokens=metadata.output_tokens,
        failure_code=failure_code,
        attempt_number=metadata.attempt_number,
    )
    session.add(invocation)
    session.flush()
    return invocation
