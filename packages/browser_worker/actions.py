from enum import StrEnum

from pydantic import BaseModel


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ApprovedCommand(BaseModel):
    action_type: str
    platform: str
    conversation_key: str
    company: str
    job_title: str
    recruiter: str
    content: str | None = None
    attachment_name: str | None = None


class ExecutionResult(BaseModel):
    outcome: ExecutionOutcome
    error_code: str | None = None
    external_reference: str | None = None
    evidence_hash: str | None = None
