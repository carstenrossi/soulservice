from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Fact(SQLModel, table=True):
    __tablename__ = "facts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    soul_id: UUID = Field(foreign_key="souls.id")
    category: str
    key: str
    value_encrypted: bytes
    value_nonce: bytes
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    confidence: float = 1.0
