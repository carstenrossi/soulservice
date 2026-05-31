from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from soulservice.core.config import settings

# Owner engine: full privileges, used by admin (soulctl) and migrations.
# The owner is a superuser and bypasses RLS — only for trusted admin paths.
engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# App engine: restricted, non-owner role that is subject to RLS. Used by the
# entire MCP runtime path (token resolution, whoami, scoped tool handlers).
app_engine = create_async_engine(
    settings.app_database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

app_session_factory = async_sessionmaker(app_engine, expire_on_commit=False)


def _validate_uuid(val: UUID) -> str:
    """Ensure we only format validated UUIDs into SET LOCAL statements."""
    return str(UUID(str(val)))


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession]:
    """Plain session without RLS context. Used by soulctl (admin)."""
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def get_scoped_session(
    tenant_id: UUID, soul_id: UUID
) -> AsyncGenerator[AsyncSession]:
    """Session with RLS context set via SET LOCAL. Used by MCP tool handlers.

    Runs as the restricted ``soulservice_app`` role so the tenant_soul_isolation
    policies are actually enforced.
    """
    tid = _validate_uuid(tenant_id)
    sid = _validate_uuid(soul_id)
    async with app_session_factory() as session, session.begin():
        await session.execute(text(f"SET LOCAL app.current_tenant = '{tid}'"))
        await session.execute(text(f"SET LOCAL app.current_soul = '{sid}'"))
        yield session
