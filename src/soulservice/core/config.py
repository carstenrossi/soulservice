from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = (
        "postgresql+asyncpg://soulservice:changeme@localhost:6000/soulservice"
    )
    soulservice_master_key: str = ""

    soulservice_host: str = "0.0.0.0"
    soulservice_port: int = 8000
    soulservice_log_level: str = "info"

    mistral_api_key: str = ""

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
