"""Envelope encryption: Master Key → DEK per Soul → AES-256-GCM on content."""

from __future__ import annotations

import os
import time
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from soulservice.core.config import settings

NONCE_SIZE = 12  # 96 bits for AES-GCM
DEK_SIZE = 32  # 256 bits


class DEKCache:
    """In-memory cache for decrypted DEKs with TTL."""

    def __init__(self, ttl: int = settings.dek_cache_ttl_seconds) -> None:
        self._cache: dict[UUID, tuple[bytes, float]] = {}
        self._ttl = ttl

    def get(self, soul_id: UUID) -> bytes | None:
        entry = self._cache.get(soul_id)
        if entry is None:
            return None
        dek, cached_at = entry
        if time.monotonic() - cached_at > self._ttl:
            self._cache.pop(soul_id, None)
            return None
        return dek

    def put(self, soul_id: UUID, dek: bytes) -> None:
        self._cache[soul_id] = (dek, time.monotonic())

    def invalidate(self, soul_id: UUID) -> None:
        self._cache.pop(soul_id, None)

    def clear(self) -> None:
        self._cache.clear()


dek_cache = DEKCache()


def generate_dek() -> bytes:
    return os.urandom(DEK_SIZE)


def encrypt_dek(dek: bytes) -> bytes:
    """Encrypt a DEK with the master key."""
    master = settings.master_key_bytes
    nonce = os.urandom(NONCE_SIZE)
    aes = AESGCM(master)
    ct = aes.encrypt(nonce, dek, None)
    return nonce + ct


def decrypt_dek(dek_encrypted: bytes) -> bytes:
    """Decrypt a DEK using the master key."""
    master = settings.master_key_bytes
    nonce = dek_encrypted[:NONCE_SIZE]
    ct = dek_encrypted[NONCE_SIZE:]
    aes = AESGCM(master)
    return aes.decrypt(nonce, ct, None)


def encrypt_content(plaintext: str, dek: bytes) -> tuple[bytes, bytes]:
    """Encrypt content with a DEK. Returns (ciphertext, nonce)."""
    nonce = os.urandom(NONCE_SIZE)
    aes = AESGCM(dek)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return ct, nonce


def decrypt_content(ciphertext: bytes, nonce: bytes, dek: bytes) -> str:
    """Decrypt content with a DEK. Returns plaintext string."""
    aes = AESGCM(dek)
    return aes.decrypt(nonce, ciphertext, None).decode("utf-8")
