from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    plan: str = "personal"
    settings: dict = Field(default_factory=dict, sa_type_kwargs={"astext_type": None})


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    email: str | None = None
    display_name: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
