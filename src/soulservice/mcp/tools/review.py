"""Review tools: list_proposals, decide."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.crypto import build_aad, decrypt_content, decrypt_dek, dek_cache

VALID_ACTIONS = ("confirm", "reject")


async def _get_dek(session: AsyncSession, soul_id: UUID) -> bytes:
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
    dek = decrypt_dek(bytes(result["dek_encrypted"]), build_aad(soul_id, "dek"))
    dek_cache.put(soul_id, dek)
    return dek


async def list_proposals(
    session: AsyncSession,
    soul_id: UUID,
    *,
    status: str = "pending",
    limit: int = 20,
) -> str:
    """List memory proposals for review."""
    dek = await _get_dek(session, soul_id)

    rows = await session.execute(
        text("""
            SELECT id, content_encrypted, content_nonce, created_at,
                   salience, tags, injection_flags, source_client
            FROM memories
            WHERE soul_id = :sid AND status = :st
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {"sid": str(soul_id), "st": status, "lim": limit},
    )
    results = rows.mappings().all()

    if not results:
        return f"No {status} proposals."

    parts = []
    aad = build_aad(soul_id, "memory")
    for row in results:
        plaintext = decrypt_content(
            bytes(row["content_encrypted"]),
            bytes(row["content_nonce"]),
            dek,
            aad,
        )
        mem_id = str(row["id"])[:8]
        created = row["created_at"].strftime("%Y-%m-%d %H:%M")
        flags = row["injection_flags"] or []
        flag_str = f" [FLAGGED: {', '.join(flags)}]" if flags else ""
        tags = row["tags"] or []
        tag_str = f" tags={tags}" if tags else ""

        parts.append(
            f"[{mem_id}] {created} salience={row['salience']:.1f}"
            f"{tag_str}{flag_str}\n  {plaintext}"
        )

    header = f"{len(results)} {status} proposal(s):\n\n"
    return header + "\n\n".join(parts)


async def decide_proposal(
    session: AsyncSession,
    soul_id: UUID,
    memory_id: str,
    action: str,
    *,
    note: str | None = None,
) -> str:
    """Confirm or reject a memory proposal."""
    if action not in VALID_ACTIONS:
        return f"Error: action must be one of {VALID_ACTIONS}."

    row = await session.execute(
        text("""
            SELECT id, status FROM memories
            WHERE id = :mid AND soul_id = :sid
        """),
        {"mid": memory_id, "sid": str(soul_id)},
    )
    result = row.mappings().first()
    if result is None:
        return "Error: memory not found."
    if result["status"] != "pending":
        return f"Error: memory is already '{result['status']}', not pending."

    new_status = "confirmed" if action == "confirm" else "rejected"

    await session.execute(
        text("UPDATE memories SET status = :st WHERE id = :mid"),
        {"st": new_status, "mid": memory_id},
    )

    return f"Memory {str(memory_id)[:8]}... {new_status}."
