from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    tenant_id: str | None = None
    user_id: str | None = None
    soul_id: str | None = None
    token_id: str | None = None
    tool_name: str | None = None
    args_hash: str | None = None
    result_size: int | None = None
    status: str | None = None
    source_ip: str | None = None
    source_client: str | None = None
