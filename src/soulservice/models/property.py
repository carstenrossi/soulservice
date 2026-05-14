from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class SoulProperty(SQLModel, table=True):
    __tablename__ = "soul_properties"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    soul_id: UUID = Field(foreign_key="souls.id")
    property_type: str
    schema_version: int
    value: dict = Field(default_factory=dict)
    is_sensitive: bool = False
    value_encrypted: bytes | None = None
    value_nonce: bytes | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
