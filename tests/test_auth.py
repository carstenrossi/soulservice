"""Tests for auth module."""

from __future__ import annotations

from uuid import uuid4

from soulservice.core.auth import (
    TokenIdentity,
    generate_token,
    has_scope,
    needs_rehash,
    verify_token,
)


def _identity(scopes: list[str]) -> TokenIdentity:
    return TokenIdentity(
        tenant_id=uuid4(),
        user_id=uuid4(),
        soul_id=uuid4(),
        token_id=uuid4(),
        scopes=scopes,
    )


def test_token_generation():
    full, prefix, hash_ = generate_token("dev")
    assert full.startswith("sol_dev_")
    assert len(prefix) == 8
    assert prefix == full[:8]
    assert hash_.startswith("$argon2id$")


def test_token_verification():
    full, _, hash_ = generate_token("dev")
    assert verify_token(full, hash_) is True
    assert verify_token("wrong_token", hash_) is False


def test_rehash_not_needed():
    _, _, hash_ = generate_token("dev")
    assert needs_rehash(hash_) is False


def test_has_scope_read_write():
    identity = _identity(["read", "write"])
    assert has_scope(identity, "read") is True
    assert has_scope(identity, "write") is True


def test_has_scope_read_only():
    identity = _identity(["read"])
    assert has_scope(identity, "read") is True
    assert has_scope(identity, "write") is False


def test_has_scope_empty():
    identity = _identity([])
    assert has_scope(identity, "read") is False
    assert has_scope(identity, "write") is False
