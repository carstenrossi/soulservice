from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


class Memory(SQLModel, table=True):
    __tablename__ = "memories"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    soul_id: UUID = Field(foreign_key="souls.id")
    content_encrypted: bytes
    content_nonce: bytes
    # embedding: pgvector field – handled via raw SQL / pgvector extension
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_recalled_at: datetime | None = None
    recall_count: int = 0
    source_client: str | None = None
    salience: float = 0.5
    status: str = "pending"
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(Text), nullable=False, server_default=text("'{}'")),
    )
    injection_flags: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(Text), nullable=False, server_default=text("'{}'")),
    )
