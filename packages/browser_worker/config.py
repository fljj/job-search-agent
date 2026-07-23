from pydantic import BaseModel


class PlatformSelectors(BaseModel):
    allowed_hosts: list[str]
    login_marker: str
    verification_marker: str
    job_list_root: str
    job_list_items: str
    job_list_item_id_attribute: str
    job_list_item_title: str
    job_list_item_company: str
    job_list_item_link: str
    next_cursor_attribute: str
    job_root: str
    job_id: str
    job_title: str
    company: str
    industry: str
    location: str
    work_mode: str
    salary: str
    description: str
    recruiter_on_job: str
    job_open_marker: str
    job_closed_marker: str
    platform_greeting_dialog: str
    platform_greeting_message: str
    conversation_list_root: str
    conversation_list_items: str
    conversation_list_item_id_attribute: str
    conversation_list_item_recruiter: str
    conversation_list_item_job_title: str
    conversation_list_item_company: str
    conversation_list_item_unread_attribute: str
    conversation_root: str
    conversation_id: str
    conversation_id_attribute: str
    recruiter: str
    conversation_job_title: str
    message_items: str
    message_id_attribute: str
    message_direction_attribute: str
    message_outbound_class: str
    message_time_attribute: str
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
