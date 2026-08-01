from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class SelectorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def reject_empty_strings(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("浏览器选择器配置不得使用空字符串")
        return value


class SessionSelectors(SelectorModel):
    login_marker: str
    verification_marker: str
    pending_user_input: str | None = None
    blocking_dialog_marker: str | None = None


class JobListSelectors(SelectorModel):
    job_list_root: str
    job_list_items: str
    job_list_item_id_attribute: str
    job_list_item_title: str
    job_list_item_company: str
    job_list_item_link: str
    next_cursor_attribute: str


class JobDetailSelectors(SelectorModel):
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
    recruiter_role_on_job: str | None = None
    job_open_marker: str
    job_closed_marker: str
    platform_greeting_dialog: str
    platform_greeting_message: str


class ConversationSelectors(SelectorModel):
    conversation_list_root: str
    conversation_list_items: str
    conversation_list_item_id_attribute: str
    conversation_list_item_id_json_key: str | None = None
    conversation_list_item_recruiter: str
    conversation_list_item_job_title: str
    conversation_list_item_company: str
    conversation_list_item_unread_attribute: str
    conversation_list_item_unread_selector: str | None = None
    conversation_list_item_job_id_attribute: str
    conversation_list_item_last_message_id_attribute: str
    conversation_list_item_last_message: str | None = None
    conversation_list_item_last_message_time: str | None = None
    conversation_list_requires_last_message_id: bool = False
    conversation_list_item_category_attribute: str
    conversation_root: str
    conversation_id: str
    conversation_id_attribute: str
    conversation_id_json_key: str | None = None
    recruiter: str
    conversation_company: str | None = None
    conversation_company_separator: str | None = None
    conversation_job_title: str
    conversation_job_link: str
    message_items: str
    message_id_attribute: str
    message_direction_attribute: str
    message_outbound_class: str
    message_time_attribute: str
    message_content: str
    message_composer: str
    message_send_button: str
    sent_message_items: str
    consent_cards: str | None = None
    consent_card_title: str | None = None
    consent_card_buttons: str | None = None
    location_consent_cards: str | None = None
    location_consent_title: str | None = None
    location_consent_detail: str | None = None
    location_consent_button: str | None = None


class ResumeActionSelectors(SelectorModel):
    resume_trigger: str
    resume_direct_confirm_button: str | None = None
    resume_items: str
    resume_confirm_button: str
    sent_resume_items: str


class PlatformSelectors(SelectorModel):
    version: str
    allowed_hosts: list[str]
    login_marker: str
    verification_marker: str
    pending_user_input: str | None = None
    blocking_dialog_marker: str | None = None
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
    recruiter_role_on_job: str | None = None
    job_open_marker: str
    job_closed_marker: str
    platform_greeting_dialog: str
    platform_greeting_message: str
    conversation_list_root: str
    conversation_list_items: str
    conversation_list_item_id_attribute: str
    conversation_list_item_id_json_key: str | None = None
    conversation_list_item_recruiter: str
    conversation_list_item_job_title: str
    conversation_list_item_company: str
    conversation_list_item_unread_attribute: str
    conversation_list_item_unread_selector: str | None = None
    conversation_list_item_job_id_attribute: str
    conversation_list_item_last_message_id_attribute: str
    conversation_list_item_last_message: str | None = None
    conversation_list_item_last_message_time: str | None = None
    conversation_list_requires_last_message_id: bool = False
    conversation_list_item_category_attribute: str
    conversation_root: str
    conversation_id: str
    conversation_id_attribute: str
    conversation_id_json_key: str | None = None
    recruiter: str
    conversation_company: str | None = None
    conversation_company_separator: str | None = None
    conversation_job_title: str
    conversation_job_link: str
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
    resume_direct_confirm_button: str | None = None
    resume_items: str
    resume_confirm_button: str
    sent_resume_items: str
    consent_cards: str | None = None
    consent_card_title: str | None = None
    consent_card_buttons: str | None = None
    location_consent_cards: str | None = None
    location_consent_title: str | None = None
    location_consent_detail: str | None = None
    location_consent_button: str | None = None


class PlatformSelectorDocument(SelectorModel):
    platform: Literal["BOSS", "MAIMAI", "LIEPIN"]
    version: str
    allowed_hosts: list[str]
    session: SessionSelectors
    job_list: JobListSelectors
    job_detail: JobDetailSelectors
    conversation: ConversationSelectors
    resume_action: ResumeActionSelectors

    @field_validator("allowed_hosts")
    @classmethod
    def require_allowed_hosts(cls, value: list[str]) -> list[str]:
        if not value or any(not host.strip() for host in value):
            raise ValueError("浏览器平台必须配置非空允许域名")
        return value

    def to_runtime(self) -> PlatformSelectors:
        values: dict[str, object] = {
            "version": self.version,
            "allowed_hosts": self.allowed_hosts,
        }
        for capability in (
            self.session,
            self.job_list,
            self.job_detail,
            self.conversation,
            self.resume_action,
        ):
            values.update(capability.model_dump())
        return PlatformSelectors.model_validate(values)


class BrowserSelectorsConfig(SelectorModel):
    version: str
    platforms: dict[str, PlatformSelectors]
