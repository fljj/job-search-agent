import sys
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "job-search-agent"
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://job_agent:job_agent@localhost:55432/job_agent"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    llm_provider: str = "ZHIPU"
    llm_api_key: SecretStr | None = None
    zhipu_api_key: SecretStr | None = None
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_model: str = "glm-5.2"
    llm_timeout_seconds: int = Field(default=30, ge=1, le=300)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    agent_lease_seconds: int = Field(default=30, ge=5, le=300)
    agent_tick_batch_size: int = Field(default=10, ge=1, le=100)
    boss_job_batch_size: int = Field(default=5, ge=1, le=20)
    boss_job_scan_interval_seconds: int = Field(default=180, ge=30, le=3600)
    boss_llm_retry_base_seconds: int = Field(default=300, ge=60, le=3600)
    boss_llm_retry_max_seconds: int = Field(default=3600, ge=300, le=21600)
    boss_job_retry_max_attempts: int = Field(default=5, ge=1, le=10)
    agent_failure_threshold: int = Field(default=3, ge=1, le=20)
    agent_poll_interval_seconds: int = Field(default=10, ge=1, le=300)
    agent_executor_mode: Literal["REAL", "FAKE"] = "REAL"
    boss_job_search_labels: str = "推荐,Java,区块链工程师"
    calendar_provider: Literal["APPLE", "GOOGLE", "MOCK"] = "APPLE"
    apple_calendar_name: str = "求职面试"
    google_calendar_access_token: SecretStr | None = None
    google_calendar_id: str = "primary"
    calendar_timeout_seconds: int = Field(default=10, ge=1, le=60)
    worker_stale_seconds: int = Field(default=60, ge=15, le=600)
    reconciliation_timeout_minutes: int = Field(default=60, ge=5, le=1440)
    reconciliation_batch_size: int = Field(default=10, ge=1, le=100)
    audit_retention_days: int = Field(default=365, ge=30, le=3650)
    run_event_retention_days: int = Field(default=90, ge=7, le=3650)
    agent_log_dir: str = "~/Desktop/job-search-agent/logs"
    agent_log_max_bytes: int = Field(default=20_000_000, ge=1_000_000)
    agent_log_backup_count: int = Field(default=14, ge=1, le=100)

    @property
    def llm_configured(self) -> bool:
        key = self.selected_llm_api_key
        return key is not None and bool(key.get_secret_value())

    @property
    def selected_llm_api_key(self) -> SecretStr | None:
        return self.zhipu_api_key if self.llm_provider.upper() == "ZHIPU" else self.llm_api_key

    @property
    def calendar_configured(self) -> bool:
        if self.calendar_provider == "MOCK":
            return True
        if self.calendar_provider == "APPLE":
            return sys.platform == "darwin" and bool(self.apple_calendar_name.strip())
        token = self.google_calendar_access_token
        return token is not None and bool(token.get_secret_value())

    @property
    def boss_job_searches(self) -> list[str]:
        return [
            label.strip()
            for label in self.boss_job_search_labels.split(",")
            if label.strip()
        ]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """重新读取运行配置，供受控的 LLM 配置热重载使用。"""
    get_settings.cache_clear()
    return get_settings()
