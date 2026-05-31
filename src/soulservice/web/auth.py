"""Web UI auth: magic-link generation/verification and session helpers."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.config import settings

SESSION_KEY = "admin_email"
ROLE_SESSION_KEY = "admin_role"

# Role hierarchy for RBAC. Higher number = more privileges.
ROLE_RANK: dict[str, int] = {"viewer": 1, "editor": 2, "admin": 3}


class NotAuthenticatedError(Exception):
    """Raised by login_required when there is no valid session."""


class NotAuthorizedError(Exception):
    """Raised by require_role when the session role is insufficient."""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_allowed_email(email: str) -> bool:
    return email.strip().lower() in settings.web_admin_email_set


async def create_magic_link_token(session: AsyncSession, email: str) -> str:
    """Create a one-time login token, store only its hash, return the raw token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.web_magic_link_ttl_minutes
    )
    await session.execute(
        text(
            "INSERT INTO web_login_tokens (token_hash, email, expires_at) "
            "VALUES (:h, :e, :exp)"
        ),
        {"h": _hash_token(token), "e": email.strip().lower(), "exp": expires_at},
    )
    await session.commit()
    return token


async def consume_magic_link_token(session: AsyncSession, token: str) -> str | None:
    """Validate and burn a one-time token. Returns the email, or None."""
    row = await session.execute(
        text(
            "SELECT email, expires_at, used_at FROM web_login_tokens "
            "WHERE token_hash = :h"
        ),
        {"h": _hash_token(token)},
    )
    result = row.mappings().first()
    if result is None or result["used_at"] is not None:
        return None
    if result["expires_at"] < datetime.now(UTC):
        return None
    await session.execute(
        text("UPDATE web_login_tokens SET used_at = NOW() WHERE token_hash = :h"),
        {"h": _hash_token(token)},
    )
    await session.commit()
    return result["email"]


def login_required(request: Request) -> str:
    """FastAPI dependency: enforce an authenticated admin session."""
    email = request.session.get(SESSION_KEY)
    if not email:
        raise NotAuthenticatedError
    return email


def current_role(request: Request) -> str:
    """Return the session role, defaulting to least privilege."""
    return request.session.get(ROLE_SESSION_KEY, "viewer")


def require_role(min_role: str):
    """Build a dependency that requires at least ``min_role`` privileges."""
    required = ROLE_RANK[min_role]

    def _dep(request: Request) -> str:
        email = request.session.get(SESSION_KEY)
        if not email:
            raise NotAuthenticatedError
        role = request.session.get(ROLE_SESSION_KEY, "viewer")
        if ROLE_RANK.get(role, 0) < required:
            raise NotAuthorizedError
        return email

    return _dep
