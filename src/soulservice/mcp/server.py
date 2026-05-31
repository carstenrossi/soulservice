"""Soulservice MCP Server – using the official mcp SDK."""

from __future__ import annotations

import logging
from contextvars import ContextVar

from sqlalchemy import text
import uvicorn

from mcp.server.fastmcp import FastMCP

from soulservice.core.config import settings
from soulservice.core.db import async_session_factory, get_scoped_session
from soulservice.core.audit import log_tool_call
from soulservice.core.auth import TokenIdentity
from soulservice.core.ratelimit import rate_limiter
from soulservice.mcp.middleware import resolve_bearer_token
from soulservice.mcp.tools.identity import get_self_core, get_relationship_overview
from soulservice.mcp.tools.memory import (
    recall as memory_recall,
    recall_recent as memory_recall_recent,
    remember_this as memory_remember_this,
)
from soulservice.mcp.tools.review import (
    decide_proposal as review_decide,
    list_proposals as review_list_proposals,
)
from soulservice.mcp.tools.facts import (
    forget_fact as facts_forget,
    get_facts as facts_get,
    learn_fact as facts_learn,
)
from soulservice.mcp.tools.properties import (
    delete_property as properties_delete,
    get_properties as properties_get,
    set_property as properties_set,
)
from soulservice.mcp.tools.meta import health_check, whoami_info

logger = logging.getLogger("soulservice.mcp")

current_identity: ContextVar[TokenIdentity | None] = ContextVar(
    "current_identity", default=None
)

mcp = FastMCP(
    "Soulservice",
    instructions=(
        "You have access to a character context service. "
        "Call who_are_you() to load the character profile for this session, "
        "then whats_our_history() for shared context. "
        "Use this information to inform your tone and responses."
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


def _require_identity() -> TokenIdentity:
    identity = current_identity.get()
    if identity is None:
        msg = "Not authenticated. Provide a valid Bearer token."
        raise ValueError(msg)
    allowed, retry = rate_limiter.check(identity.token_id)
    if not allowed:
        msg = f"Rate limit exceeded. Retry after {retry:.1f}s."
        raise ValueError(msg)
    return identity


# ── Tools ────────────────────────────────────────────────────────


@mcp.tool()
async def health() -> dict:
    """Server health check. Returns status."""
    return health_check()


@mcp.tool()
async def who_are_you() -> str:
    """Load the character profile for this session. Call this first."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await get_self_core(session, identity.soul_id, mode=identity.mode)

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
    """Load relationship context and shared history. Call after who_are_you()."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await get_relationship_overview(
            session, identity.soul_id, mode=identity.mode
        )

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
async def remember_this(content: str, tags: list[str] | None = None, salience: float = 0.5) -> str:
    """Note something from the conversation worth keeping. Stored as pending proposal for human review."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await memory_remember_this(
            session,
            identity.tenant_id,
            identity.soul_id,
            content,
            tags=tags,
            salience=salience,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="remember_this",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def recall(query: str, k: int = 5) -> str:
    """Search through past conversation notes by meaning."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await memory_recall(session, identity.soul_id, query, k=k)

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="recall",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def recall_recent(days: int = 7) -> str:
    """Get recent conversation notes (chronological)."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await memory_recall_recent(session, identity.soul_id, days=days)

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="recall_recent",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def list_proposals(status: str = "pending") -> str:
    """List conversation notes pending human review."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await review_list_proposals(
            session, identity.soul_id, status=status
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="list_proposals",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def decide(proposal_id: str, action: str, note: str | None = None) -> str:
    """Approve or reject a conversation note. action: 'confirm' or 'reject'."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await review_decide(
            session, identity.soul_id, proposal_id, action, note=note
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="decide",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def learn_fact(category: str, key: str, value: str, confidence: float = 1.0) -> str:
    """Store or update a structured fact (e.g. user preferences, known context)."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await facts_learn(
            session,
            identity.tenant_id,
            identity.soul_id,
            category,
            key,
            value,
            confidence=confidence,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="learn_fact",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def get_facts(category: str | None = None) -> str:
    """Retrieve stored facts, optionally filtered by category."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await facts_get(
            session,
            identity.soul_id,
            category=category,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="get_facts",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def forget_fact(category: str, key: str) -> str:
    """Remove a stored fact that is no longer accurate."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await facts_forget(
            session,
            identity.soul_id,
            category,
            key,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="forget_fact",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def set_property(property_type: str, value: dict) -> str:
    """Store or update a typed property (e.g. communication_style, boundaries)."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await properties_set(
            session,
            identity.tenant_id,
            identity.soul_id,
            property_type,
            value,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="set_property",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def get_properties(property_type: str | None = None) -> str:
    """Retrieve stored properties, optionally filtered by type."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await properties_get(
            session,
            identity.soul_id,
            property_type=property_type,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="get_properties",
            result_size=len(result),
        )

    return result


@mcp.tool()
async def delete_property(property_type: str) -> str:
    """Soft-delete a property that no longer applies."""
    try:
        identity = _require_identity()
    except ValueError as e:
        return f"Error: {e}"

    async with get_scoped_session(identity.tenant_id, identity.soul_id) as session:
        result = await properties_delete(
            session,
            identity.soul_id,
            property_type,
        )

        await log_tool_call(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            soul_id=identity.soul_id,
            token_id=identity.token_id,
            tool_name="delete_property",
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
