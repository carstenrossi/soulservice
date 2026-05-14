"""Tests for auth module."""

from __future__ import annotations

from soulservice.core.auth import generate_token, needs_rehash, verify_token


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
