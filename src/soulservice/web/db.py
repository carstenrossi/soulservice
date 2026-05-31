"""Session helpers for the web UI.

The admin UI no longer runs as the DB owner. Instead it mirrors the MCP runtime:
- Cross-soul / non-RLS reads (souls list, soul resolution) use the restricted
  ``soulservice_app`` role via ``app_session_factory`` (no RLS context needed,
  these tables have no per-soul policy).
- Per-soul work runs inside ``get_scoped_session`` which sets the RLS GUCs and
  owns the transaction (commit-on-exit). Query functions therefore must NOT call
  ``session.commit()`` themselves.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.db import app_session_factory, get_scoped_session
from soulservice.web import queries


@asynccontextmanager
async def soul_context(
    slug: str,
) -> AsyncGenerator[tuple[dict, list, AsyncSession]]:
    """Resolve a soul (app role), then yield (soul, souls, RLS-scoped session).

    ``souls`` is the full list for the soul selector. The yielded ``session`` is
    scoped to the resolved soul and enforces row-level security.
    """
    async with app_session_factory() as bootstrap:
        soul = await queries.resolve_soul(bootstrap, slug)
        souls = await queries.list_souls(bootstrap)
    async with get_scoped_session(soul["tenant_id"], soul["id"]) as session:
        yield soul, souls, session
