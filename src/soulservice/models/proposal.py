from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Proposal(SQLModel, table=True):
    __tablename__ = "proposals"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    soul_id: UUID = Field(foreign_key="souls.id")
    kind: str
    payload_encrypted: bytes
    payload_nonce: bytes
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "pending"
    reviewed_at: datetime | None = None
    source_client: str | None = None
