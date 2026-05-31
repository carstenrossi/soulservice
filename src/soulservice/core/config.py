from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_APP_DATABASE_URL = (
    "postgresql+asyncpg://soulservice_app:soulservice_app_pw@localhost:6000/soulservice"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = (
        "postgresql+asyncpg://soulservice:changeme@localhost:6000/soulservice"
    )
    # Restricted runtime connection (non-owner role, subject to RLS).
    # Used by the MCP runtime path; admin/migrations use database_url.
    app_database_url: str = _DEFAULT_APP_DATABASE_URL
    soulservice_master_key: str = ""

    @field_validator("app_database_url")
    @classmethod
    def _fallback_app_database_url(cls, value: str) -> str:
        # An empty value (e.g. the .env.example placeholder) falls back to the
        # local dev role bootstrapped by infra/init.sql.
        return value or _DEFAULT_APP_DATABASE_URL

    soulservice_host: str = "0.0.0.0"
    soulservice_port: int = 8000
    soulservice_log_level: str = "info"

    mistral_api_key: str = ""
    anthropic_api_key: str = ""
    chat_mcp_token: str = ""

    # Crypto
    dek_cache_ttl_seconds: int = 3600  # 1 hour

    # Rate limiting
    rate_limit_per_minute: int = 100
    rate_limit_per_hour: int = 1000

    # Token defaults
    token_max_age_days: int = 90

    @property
    def master_key_bytes(self) -> bytes:
        import base64

        if not self.soulservice_master_key:
            msg = "SOULSERVICE_MASTER_KEY is not set"
            raise ValueError(msg)
        return base64.b64decode(self.soulservice_master_key)


settings = Settings()
