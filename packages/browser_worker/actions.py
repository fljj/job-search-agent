from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ApprovedCommand(BaseModel):
    action_type: str
    platform: str
    conversation_key: str | None = None
    external_job_id: str | None = None
    company: str
    job_title: str
    recruiter: str
    content: str | None = None
    delivery_mode: str = "CUSTOM"
    expected_platform_content: str | None = None
    attachment_name: str | None = None


class ExecutionResult(BaseModel):
    outcome: ExecutionOutcome
    error_code: str | None = None
    external_reference: str | None = None
    evidence_hash: str | None = None
    observed_content: str | None = None


class ActionExecutor(Protocol):
    def execute(self, cdp_url: str, command: ApprovedCommand) -> ExecutionResult: ...
