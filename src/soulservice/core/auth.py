"""Token authentication: generation, hashing (Argon2id), verification."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

TOKEN_PREFIX_LEN = 8
TOKEN_RANDOM_BYTES = 32


VALID_MODES = ("identity", "messenger")


@dataclass(frozen=True)
class TokenIdentity:
    """Resolved identity from a valid API token."""

    tenant_id: UUID
    user_id: UUID
    soul_id: UUID
    token_id: UUID
    scopes: list[str]
    mode: str = "identity"


def generate_token(env: str = "dev") -> tuple[str, str, str]:
    """Generate a new API token.

    Returns (full_token, token_prefix, token_hash).
    The full token is shown once to the user; only the hash is stored.
    """
    random_part = secrets.token_hex(TOKEN_RANDOM_BYTES)
    full_token = f"sol_{env}_{random_part}"
    prefix = full_token[:TOKEN_PREFIX_LEN]
    token_hash = _ph.hash(full_token)
    return full_token, prefix, token_hash


def has_scope(identity: TokenIdentity, scope: str) -> bool:
    """Whether the resolved token identity carries the given scope."""
    return scope in identity.scopes


def verify_token(token: str, token_hash: str) -> bool:
    """Verify a token against its stored Argon2id hash."""
    try:
        return _ph.verify(token_hash, token)
    except VerifyMismatchError:
        return False


def needs_rehash(token_hash: str) -> bool:
    """Check if a hash needs rehashing due to parameter changes."""
    return _ph.check_needs_rehash(token_hash)
