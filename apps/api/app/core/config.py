from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "job-search-agent"
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://job_agent:job_agent@localhost:55432/job_agent"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    llm_provider: str = "QWEN"
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_timeout_seconds: int = Field(default=30, ge=1, le=300)
    llm_max_retries: int = Field(default=1, ge=0, le=3)

    @property
    def llm_configured(self) -> bool:
        return self.llm_api_key is not None and bool(self.llm_api_key.get_secret_value())

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
