from pydantic import BaseModel


class PlatformSelectors(BaseModel):
    allowed_hosts: list[str]
    login_marker: str
    verification_marker: str
    job_root: str
    job_id: str
    job_title: str
    company: str
    industry: str
    location: str
    work_mode: str
    salary: str
    description: str
    conversation_root: str
    conversation_id: str
    recruiter: str
    message_items: str
    message_id_attribute: str
    message_content: str


class BrowserSelectorsConfig(BaseModel):
    version: str
    platforms: dict[str, PlatformSelectors]
