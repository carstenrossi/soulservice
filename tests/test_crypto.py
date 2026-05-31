"""Tests for core crypto module."""

from __future__ import annotations

from uuid import uuid4

import pytest

from soulservice.core.crypto import (
    build_aad,
    decrypt_content,
    decrypt_dek,
    encrypt_content,
    encrypt_dek,
    generate_dek,
)


def test_dek_roundtrip():
    soul_id = uuid4()
    aad = build_aad(soul_id, "dek")
    dek = generate_dek()
    encrypted = encrypt_dek(dek, aad)
    decrypted = decrypt_dek(encrypted, aad)
    assert dek == decrypted


def test_dek_wrong_aad_fails():
    soul_id = uuid4()
    dek = generate_dek()
    encrypted = encrypt_dek(dek, build_aad(soul_id, "dek"))
    with pytest.raises(Exception):  # noqa: B017
        decrypt_dek(encrypted, build_aad(uuid4(), "dek"))


def test_content_roundtrip():
    dek = generate_dek()
    aad = build_aad(uuid4(), "memory")
    plaintext = "George remembers the afternoon in May."
    ct, nonce = encrypt_content(plaintext, dek, aad)
    result = decrypt_content(ct, nonce, dek, aad)
    assert result == plaintext


def test_different_nonces():
    dek = generate_dek()
    aad = build_aad(uuid4(), "memory")
    _, nonce1 = encrypt_content("a", dek, aad)
    _, nonce2 = encrypt_content("a", dek, aad)
    assert nonce1 != nonce2


def test_wrong_dek_fails():
    dek1 = generate_dek()
    dek2 = generate_dek()
    aad = build_aad(uuid4(), "fact")
    ct, nonce = encrypt_content("secret", dek1, aad)
    with pytest.raises(Exception):  # noqa: B017
        decrypt_content(ct, nonce, dek2, aad)


def test_wrong_domain_aad_fails():
    """Ciphertext from one domain must not decrypt under another domain."""
    dek = generate_dek()
    soul_id = uuid4()
    ct, nonce = encrypt_content("secret", dek, build_aad(soul_id, "fact"))
    with pytest.raises(Exception):  # noqa: B017
        decrypt_content(ct, nonce, dek, build_aad(soul_id, "property"))


def test_wrong_soul_aad_fails():
    """Ciphertext bound to one soul must not decrypt under another soul."""
    dek = generate_dek()
    ct, nonce = encrypt_content("secret", dek, build_aad(uuid4(), "memory"))
    with pytest.raises(Exception):  # noqa: B017
        decrypt_content(ct, nonce, dek, build_aad(uuid4(), "memory"))
