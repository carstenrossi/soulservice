"""Envelope encryption: Master Key → DEK per Soul → AES-256-GCM on content."""

from __future__ import annotations

import os
import time
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from soulservice.core.config import settings

NONCE_SIZE = 12  # 96 bits for AES-GCM
DEK_SIZE = 32  # 256 bits


def build_aad(soul_id: UUID, domain: str) -> bytes:
    """Build AES-GCM associated data binding ciphertext to a soul + domain.

    The AAD is authenticated (not encrypted) and verified on decrypt, so a
    ciphertext cannot be moved between souls or between record types (domains).

    WARNING: this format and the domain labels are part of the on-disk crypto
    contract. Changing either makes all existing ciphertext undecryptable, just
    like rotating a key. Never change them without a re-encryption migration.
    """
    return soul_id.bytes + b"|" + domain.encode("ascii")


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


def encrypt_dek(dek: bytes, aad: bytes) -> bytes:
    """Encrypt a DEK with the master key, bound to the given AAD."""
    master = settings.master_key_bytes
    nonce = os.urandom(NONCE_SIZE)
    aes = AESGCM(master)
    ct = aes.encrypt(nonce, dek, aad)
    return nonce + ct


def decrypt_dek(dek_encrypted: bytes, aad: bytes) -> bytes:
    """Decrypt a DEK using the master key, verifying the given AAD."""
    master = settings.master_key_bytes
    nonce = dek_encrypted[:NONCE_SIZE]
    ct = dek_encrypted[NONCE_SIZE:]
    aes = AESGCM(master)
    return aes.decrypt(nonce, ct, aad)


def encrypt_content(plaintext: str, dek: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Encrypt content with a DEK, bound to the given AAD. Returns (ct, nonce)."""
    nonce = os.urandom(NONCE_SIZE)
    aes = AESGCM(dek)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return ct, nonce


def decrypt_content(ciphertext: bytes, nonce: bytes, dek: bytes, aad: bytes) -> str:
    """Decrypt content with a DEK, verifying the given AAD. Returns plaintext."""
    aes = AESGCM(dek)
    return aes.decrypt(nonce, ciphertext, aad).decode("utf-8")
