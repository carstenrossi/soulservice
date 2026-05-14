from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Soul(SQLModel, table=True):
    __tablename__ = "souls"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    owner_user_id: UUID = Field(foreign_key="users.id")
    slug: str
    display_name: str
    status: str = "active"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SoulKey(SQLModel, table=True):
    __tablename__ = "soul_keys"

    soul_id: UUID = Field(foreign_key="souls.id", primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    dek_encrypted: bytes
    key_version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SoulSelfCore(SQLModel, table=True):
    __tablename__ = "soul_self_cores"

    soul_id: UUID = Field(foreign_key="souls.id", primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    content_encrypted: bytes
    content_nonce: bytes
    current_version: int = 1
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: UUID | None = Field(default=None, foreign_key="users.id")


class SoulSelfCoreHistory(SQLModel, table=True):
    __tablename__ = "soul_self_core_history"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    soul_id: UUID = Field(foreign_key="souls.id")
    tenant_id: UUID = Field(foreign_key="tenants.id")
    version: int
    content_encrypted: bytes
    content_nonce: bytes
    changed_at: datetime = Field(default_factory=datetime.utcnow)
    changed_by: UUID | None = Field(default=None, foreign_key="users.id")
    change_note: str | None = None
