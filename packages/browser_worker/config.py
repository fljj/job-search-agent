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
    message_composer: str
    message_send_button: str
    sent_message_items: str
    resume_trigger: str
    resume_items: str
    resume_confirm_button: str
    sent_resume_items: str


class BrowserSelectorsConfig(BaseModel):
    version: str
    platforms: dict[str, PlatformSelectors]
