"""Identity tools: who_are_you, whats_our_history."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.crypto import decrypt_content, decrypt_dek, dek_cache


async def _get_dek(session: AsyncSession, soul_id: UUID) -> bytes:
    """Load and cache the DEK for a soul."""
    cached = dek_cache.get(soul_id)
    if cached is not None:
        return cached

    row = await session.execute(
        text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
        {"sid": str(soul_id)},
    )
    result = row.mappings().first()
    if result is None:
        msg = f"No DEK found for soul {soul_id}"
        raise ValueError(msg)

    dek = decrypt_dek(bytes(result["dek_encrypted"]))
    dek_cache.put(soul_id, dek)
    return dek


async def get_self_core(session: AsyncSession, soul_id: UUID) -> str:
    """Load and decrypt the current Self Core YAML for a soul."""
    dek = await _get_dek(session, soul_id)

    row = await session.execute(
        text("""
            SELECT content_encrypted, content_nonce
            FROM soul_self_cores WHERE soul_id = :sid
        """),
        {"sid": str(soul_id)},
    )
    result = row.mappings().first()
    if result is None:
        return "# Self Core not yet configured.\n"

    return decrypt_content(
        bytes(result["content_encrypted"]),
        bytes(result["content_nonce"]),
        dek,
    )


async def get_relationship_overview(session: AsyncSession, soul_id: UUID) -> str:
    """Build a relationship overview from recent memories.

    Phase 1: returns a static placeholder.
    Phase 2+: will query memories and build a dynamic summary.
    """
    # Phase 1 placeholder in the soul's language (German for George).
    # Phase 2+ will build this dynamically from memories.
    return (
        "Wir stehen am Anfang. Ich kenne dich aus dem, was du mir in meinen "
        "Self Core mitgegeben hast, aber wir haben noch keine gemeinsamen "
        "Erinnerungen. Das kommt."
    )
