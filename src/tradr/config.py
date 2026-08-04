"""App settings from environment (see .env.example). Infra only — never personal data."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///./tradr.db"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    market_data_provider: str = "none"   # "none" | "stooq"
    market_data_api_key: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
