"""Auth middleware: resolve a Bearer token into a TokenIdentity."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from soulservice.core.auth import TokenIdentity, verify_token
from soulservice.core.db import app_session_factory


async def resolve_bearer_token(authorization: str | None) -> TokenIdentity | None:
    """Validate a Bearer token and return the resolved identity, or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("sol_"):
        return None

    async with app_session_factory() as session:
        rows = await session.execute(
            text("""
                SELECT id, tenant_id, user_id, soul_id, token_hash, scopes,
                       expires_at, revoked_at, mode
                FROM api_tokens
                WHERE token_prefix = :prefix
                  AND revoked_at IS NULL
            """),
            {"prefix": token[:8]},
        )
        candidates = rows.mappings().all()

    for row in candidates:
        if row["revoked_at"] is not None:
            continue
        if row["expires_at"] < datetime.now(timezone.utc):
            continue
        if not verify_token(token, row["token_hash"]):
            continue

        # Valid token – update last_used_at
        async with app_session_factory() as session:
            await session.execute(
                text("UPDATE api_tokens SET last_used_at = NOW() WHERE id = :tid"),
                {"tid": str(row["id"])},
            )
            await session.commit()

        return TokenIdentity(
            tenant_id=UUID(str(row["tenant_id"])),
            user_id=UUID(str(row["user_id"])),
            soul_id=UUID(str(row["soul_id"])),
            token_id=UUID(str(row["id"])),
            scopes=list(row["scopes"]),
            mode=row.get("mode", "identity") or "identity",
        )

    return None
