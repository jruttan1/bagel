from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./bagel.db"
    encryption_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    messages_api_key: str = ""
    messages_line_handle: str = ""
    messages_webhook_secret: str = ""
    messages_api_base: str = "https://api.messages.dev"
    admin_api_key: str = ""
    fmp_api_key: str = ""
    morning_brief_time: str = "07:30"
    scheduler_enabled: bool = True
    sync_stale_minutes: int = Field(default=15, ge=1, le=1440)
    webhook_tolerance_seconds: int = Field(default=300, ge=30, le=900)
    max_message_chars: int = Field(default=3500, ge=500, le=10000)

    @field_validator("database_url")
    @classmethod
    def normalize_database_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://") and "+asyncpg" not in value:
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    def validate_runtime_secrets(self) -> None:
        if self.app_env != "production":
            return
        required = {
            "ENCRYPTION_KEY": self.encryption_key,
            "OPENAI_API_KEY": self.openai_api_key,
            "MESSAGES_API_KEY": self.messages_api_key,
            "MESSAGES_LINE_HANDLE": self.messages_line_handle,
            "MESSAGES_WEBHOOK_SECRET": self.messages_webhook_secret,
            "ADMIN_API_KEY": self.admin_api_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing production secrets: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime_secrets()
    return settings

