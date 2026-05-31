"""Integration test: Row Level Security tenant/soul isolation.

Verifies that the restricted ``soulservice_app`` role is actually subject to the
``tenant_soul_isolation`` policies:
  * with an RLS context set, it sees only the matching tenant/soul rows;
  * without a context, it cannot read foreign rows (fail-closed).

Requires a running, migrated Postgres (see docker-compose). The test seeds two
tenants as the owner role and cleans them up afterwards. It is skipped when the
database is unreachable or POSTGRES_PASSWORD is not set, so the unit-test run
stays self-contained.
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio

_PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD")

_OWNER_URL = os.environ.get("TEST_OWNER_DATABASE_URL") or (
    f"postgresql+asyncpg://soulservice:{_PG_PASSWORD}@localhost:6000/soulservice"
    if _PG_PASSWORD
    else None
)
_APP_URL = os.environ.get(
    "TEST_APP_DATABASE_URL",
    "postgresql+asyncpg://soulservice_app:soulservice_app_pw@localhost:6000/soulservice",
)


async def _db_reachable() -> bool:
    if _OWNER_URL is None:
        return False
    engine = create_async_engine(_OWNER_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def _seed_tenant(session, name: str) -> tuple[UUID, UUID]:
    """Create tenant + user + soul + one memory; return (tenant_id, soul_id)."""
    tenant_id = (
        await session.execute(
            text("INSERT INTO tenants (name) VALUES (:n) RETURNING id"),
            {"n": name},
        )
    ).scalar_one()
    user_id = (
        await session.execute(
            text(
                "INSERT INTO users (tenant_id, display_name) "
                "VALUES (:t, :n) RETURNING id"
            ),
            {"t": str(tenant_id), "n": f"user-{name}"},
        )
    ).scalar_one()
    soul_id = (
        await session.execute(
            text(
                "INSERT INTO souls (tenant_id, owner_user_id, slug, display_name) "
                "VALUES (:t, :u, :s, :d) RETURNING id"
            ),
            {"t": str(tenant_id), "u": str(user_id), "s": name, "d": name},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO memories (tenant_id, soul_id, content_encrypted, content_nonce) "
            "VALUES (:t, :s, '\\x00', '\\x00')"
        ),
        {"t": str(tenant_id), "s": str(soul_id)},
    )
    return UUID(str(tenant_id)), UUID(str(soul_id))


@pytest.fixture
async def seeded():
    if not await _db_reachable():
        pytest.skip("Postgres not reachable or POSTGRES_PASSWORD unset")

    owner_engine = create_async_engine(_OWNER_URL)
    owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    app_engine = create_async_engine(_APP_URL)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)

    name_a = "rls-test-tenant-a"
    name_b = "rls-test-tenant-b"
    tenant_ids: list[UUID] = []
    try:
        async with owner_factory() as session, session.begin():
            tenant_a, soul_a = await _seed_tenant(session, name_a)
            tenant_b, soul_b = await _seed_tenant(session, name_b)
        tenant_ids = [tenant_a, tenant_b]

        yield {
            "app_factory": app_factory,
            "tenant_a": tenant_a,
            "soul_a": soul_a,
            "tenant_b": tenant_b,
            "soul_b": soul_b,
        }
    finally:
        async with owner_factory() as session, session.begin():
            for tid in tenant_ids:
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :id"), {"id": str(tid)}
                )
        await app_engine.dispose()
        await owner_engine.dispose()


async def test_scoped_context_sees_only_own_tenant(seeded):
    app_factory = seeded["app_factory"]
    async with app_factory() as session, session.begin():
        # SET LOCAL does not accept bind parameters; interpolate the trusted UUID.
        await session.execute(
            text(f"SET LOCAL app.current_tenant = '{seeded['tenant_a']}'")
        )
        await session.execute(
            text(f"SET LOCAL app.current_soul = '{seeded['soul_a']}'")
        )
        rows = (
            await session.execute(text("SELECT tenant_id FROM memories"))
        ).scalars().all()

    seen = {UUID(str(r)) for r in rows}
    assert seen == {seeded["tenant_a"]}
    assert seeded["tenant_b"] not in seen


async def test_other_tenant_context_sees_only_its_own(seeded):
    app_factory = seeded["app_factory"]
    async with app_factory() as session, session.begin():
        await session.execute(
            text(f"SET LOCAL app.current_tenant = '{seeded['tenant_b']}'")
        )
        await session.execute(
            text(f"SET LOCAL app.current_soul = '{seeded['soul_b']}'")
        )
        rows = (
            await session.execute(text("SELECT tenant_id FROM memories"))
        ).scalars().all()

    seen = {UUID(str(r)) for r in rows}
    assert seen == {seeded["tenant_b"]}
    assert seeded["tenant_a"] not in seen


async def test_no_context_is_fail_closed(seeded):
    """Without an RLS context, the app role must not read foreign rows."""
    # Use a dedicated engine so the custom GUC was never set on this connection.
    engine = create_async_engine(_APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            try:
                rows = (
                    await session.execute(text("SELECT tenant_id FROM memories"))
                ).scalars().all()
            except DBAPIError:
                # Policy raised because the GUC is unset — fail-closed, acceptable.
                return
        seen = {UUID(str(r)) for r in rows}
        assert seeded["tenant_a"] not in seen
        assert seeded["tenant_b"] not in seen
    finally:
        await engine.dispose()
