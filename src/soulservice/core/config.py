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

    # Binds the MCP server to all interfaces for container/LAN deployment.
    soulservice_host: str = "0.0.0.0"  # noqa: S104
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

    # Web UI (Phase 3, admin, localhost-only)
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    web_base_url: str = "http://localhost:6002"
    web_session_secret: str = ""
    # Comma-separated allowlist. Each entry is "email" (role defaults to "admin"
    # for backwards compatibility) or "email:role" with role in viewer/editor/admin.
    web_admin_emails: str = ""
    web_magic_link_ttl_minutes: int = 10
    # Send session cookies only over HTTPS. Keep False for localhost/http; set
    # True behind TLS in production.
    web_secure_cookies: bool = False
    # Magic-link requests allowed per (client IP + email) per hour.
    web_login_rate_per_hour: int = 10
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    web_from_email: str = "soulservice@localhost"

    @property
    def web_admin_roles(self) -> dict[str, str]:
        """Map allowlisted email -> role (viewer < editor < admin)."""
        known = {"viewer", "editor", "admin"}
        roles: dict[str, str] = {}
        for entry in self.web_admin_emails.split(","):
            entry = entry.strip()
            if not entry:
                continue
            email, _, role = entry.partition(":")
            email = email.strip().lower()
            if not email:
                continue
            role = role.strip().lower() or "admin"
            # Unknown role falls back to least privilege.
            roles[email] = role if role in known else "viewer"
        return roles

    @property
    def web_admin_email_set(self) -> set[str]:
        return set(self.web_admin_roles.keys())

    @property
    def master_key_bytes(self) -> bytes:
        import base64

        if not self.soulservice_master_key:
            msg = "SOULSERVICE_MASTER_KEY is not set"
            raise ValueError(msg)
        return base64.b64decode(self.soulservice_master_key)


settings = Settings()
