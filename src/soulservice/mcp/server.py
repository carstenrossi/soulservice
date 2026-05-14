"""Soulservice MCP Server – using the official mcp SDK."""

from __future__ import annotations

import logging
from contextvars import ContextVar, copy_context

from sqlalchemy import text
import uvicorn

from mcp.server.fastmcp import FastMCP

from soulservice.core.config import settings
from soulservice.core.db import async_session_factory, get_scoped_session
from soulservice.core.audit import log_tool_call
from soulservice.core.auth import TokenIdentity
from soulservice.mcp.middleware import resolve_bearer_token
from soulservice.mcp.tools.identity import get_self_core, get_relationship_overview
from soulservice.mcp.tools.meta import health_check, whoami_info

logger = logging.getLogger("soulservice.mcp")

current_identity: ContextVar[TokenIdentity | None] = ContextVar(
    "current_identity", default=None
)

mcp = FastMCP(
    "Soulservice",
    instructions=(
        "You are connected to a Soul server. Use who_are_you() first to load "
        "the soul's identity, then whats_our_history() for relationship context."
    ),
)


# ── ASGI Auth Middleware ─────────────────────────────────────────


class BearerAuthASGIMiddleware:
    """Pure ASGI middleware – no BaseHTTPMiddleware context-var issues."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth_raw = headers.get(b"authorization", b"").decode()
            if auth_raw:
                identity = await resolve_bearer_token(auth_raw)
                current_identity.set(identity)
            else:
                current_identity.set(None)
        await self.app(scope, receive, send)


# ── Tools ────────────────────────────────────────────────────────


@mcp.tool()
async def health() -> dict:
    """Server health check. Returns status."""
    return health_check()


@mcp.tool()
async def who_are_you() -> str:
    """Load the Soul's identity (Self Core). Call this first in every conversation."""
    identity = current_identity.get()
    if identity is None:
        return "Error: not authenticated. Provide a valid Bearer token."

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await get_self_core(session, identity.soul_id)

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="who_are_you",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def whats_our_history() -> str:
    """Relationship overview and current topics. Call after who_are_you()."""
    identity = current_identity.get()
    if identity is None:
        return "Error: not authenticated. Provide a valid Bearer token."

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await get_relationship_overview(session, identity.soul_id)

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="whats_our_history",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def whoami() -> dict:
    """Which Soul, which Tenant, which User am I connected to?"""
    identity = current_identity.get()
    if identity is None:
        return {"error": "not authenticated"}

    async with async_session_factory() as session:
        row = await session.execute(
            text("""
                SELECT t.name as tenant_name, u.display_name as user_name,
                       s.slug as soul_slug, s.display_name as soul_display
                FROM souls s
                JOIN tenants t ON t.id = s.tenant_id
                JOIN users u ON u.id = s.owner_user_id
                WHERE s.id = :sid
            """),
            {"sid": str(identity.soul_id)},
        )
        info = row.mappings().first()
        if info is None:
            return {"error": "soul not found"}

        return whoami_info(
            tenant_name=info["tenant_name"],
            user_name=info["user_name"],
            soul_slug=info["soul_slug"],
            soul_display=info["soul_display"],
        )


# ── Server Entry Point ──────────────────────────────────────────


def create_app():
    """Create the ASGI app with auth middleware wrapping the MCP server."""
    mcp_app = mcp.streamable_http_app()
    return BearerAuthASGIMiddleware(mcp_app)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.soulservice_log_level.upper()),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info(
        "Starting Soulservice MCP on %s:%s",
        settings.soulservice_host,
        settings.soulservice_port,
    )
    app = create_app()
    uvicorn.run(
        app,
        host=settings.soulservice_host,
        port=settings.soulservice_port,
    )


if __name__ == "__main__":
    main()
