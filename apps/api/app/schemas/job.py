from uuid import UUID

from pydantic import BaseModel, Field

from packages.job_parser.models import JobInput, ParsedJob


class JobImportPayload(JobInput):
    pass


class BatchJobImportPayload(BaseModel):
    items: list[dict[str, object]] = Field(min_length=1, max_length=500)


class JobResponse(JobInput):
    id: UUID
    content_hash: str
    latest_score: dict[str, object] | None = None
    communication: dict[str, object] | None = None


class JobImportResponse(BaseModel):
    result: str
    job: JobResponse


class BatchJobImportItem(BaseModel):
    index: int
    result: str
    job: JobResponse | None = None
    error: str | None = None


class ParseRequest(BaseModel):
    mode: str = "RULE"


class ParsedJobResponse(ParsedJob):
    id: UUID
    job_id: UUID
