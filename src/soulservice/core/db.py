from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from soulservice.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession]:
    """Plain session without RLS context. Used by soulctl (admin)."""
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def get_scoped_session(
    tenant_id: UUID, soul_id: UUID
) -> AsyncGenerator[AsyncSession]:
    """Session with RLS context set via SET LOCAL. Used by MCP tool handlers."""
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                text("SET LOCAL app.current_tenant = :tid"),
                {"tid": str(tenant_id)},
            )
            await session.execute(
                text("SET LOCAL app.current_soul = :sid"),
                {"sid": str(soul_id)},
            )
            yield session
