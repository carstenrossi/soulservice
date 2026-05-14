from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class ApiToken(SQLModel, table=True):
    __tablename__ = "api_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    user_id: UUID = Field(foreign_key="users.id")
    soul_id: UUID = Field(foreign_key="souls.id")
    token_hash: str
    token_prefix: str
    name: str
    scopes: list[str] = Field(default=["read", "write"])
    mode: str = Field(default="identity")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None
    expires_at: datetime
    revoked_at: datetime | None = None
