"""Tests for core crypto module."""

from __future__ import annotations

from soulservice.core.crypto import (
    decrypt_content,
    decrypt_dek,
    encrypt_content,
    encrypt_dek,
    generate_dek,
)


def test_dek_roundtrip():
    dek = generate_dek()
    encrypted = encrypt_dek(dek)
    decrypted = decrypt_dek(encrypted)
    assert dek == decrypted


def test_content_roundtrip():
    dek = generate_dek()
    plaintext = "George erinnert sich an den Nachmittag im Mai."
    ct, nonce = encrypt_content(plaintext, dek)
    result = decrypt_content(ct, nonce, dek)
    assert result == plaintext


def test_different_nonces():
    dek = generate_dek()
    _, nonce1 = encrypt_content("a", dek)
    _, nonce2 = encrypt_content("a", dek)
    assert nonce1 != nonce2


def test_wrong_dek_fails():
    dek1 = generate_dek()
    dek2 = generate_dek()
    ct, nonce = encrypt_content("secret", dek1)
    try:
        decrypt_content(ct, nonce, dek2)
        assert False, "Should have raised"  # noqa: B011
    except Exception:
        pass
