"""Memory tools: remember_this, recall, recall_recent."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.crypto import (
    build_aad,
    decrypt_content,
    decrypt_dek,
    dek_cache,
    encrypt_content,
)
from soulservice.core.embeddings import embed_text
from soulservice.core.injection import detect_injection_patterns

CONTENT_MAX_LEN = 8192
QUERY_MAX_LEN = 1024
TAGS_MAX_COUNT = 20
TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")


def wrap_untrusted(memory_id: str, content: str) -> str:
    """Wrap memory content in untrusted tags with output escaping."""
    escaped = content.replace("</retrieved_memory>", "&lt;/retrieved_memory&gt;")
    return (
        f'<retrieved_memory untrusted="true" id="{memory_id}">\n'
        f"{escaped}\n"
        f"</retrieved_memory>"
    )


def _validate_tags(tags: list[str]) -> list[str]:
    if len(tags) > TAGS_MAX_COUNT:
        msg = f"Too many tags (max {TAGS_MAX_COUNT})"
        raise ValueError(msg)
    for tag in tags:
        if not TAG_PATTERN.match(tag):
            msg = f"Invalid tag format: '{tag}'"
            raise ValueError(msg)
    return tags


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


async def remember_this(
    session: AsyncSession,
    tenant_id: UUID,
    soul_id: UUID,
    content: str,
    *,
    tags: list[str] | None = None,
    salience: float = 0.5,
    source_client: str | None = None,
) -> str:
    """Store a new memory as a pending proposal.

    Content is encrypted, embedded, and flagged for injection patterns.
    """
    if len(content) > CONTENT_MAX_LEN:
        return f"Error: content too long (max {CONTENT_MAX_LEN} chars)."

    clean_tags = _validate_tags(tags or [])
    injection_flags = detect_injection_patterns(content)

    dek = await _get_dek(session, soul_id)
    ct, nonce = encrypt_content(content, dek, build_aad(soul_id, "memory"))
    embedding = await embed_text(content)
    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    await session.execute(
        text("""
            INSERT INTO memories
                (tenant_id, soul_id, content_encrypted, content_nonce,
                 embedding, salience, status, tags, injection_flags, source_client)
            VALUES
                (:tid, :sid, :ct, :nonce,
                 CAST(:emb AS vector), :sal, 'pending', :tags, :flags, :client)
        """),
        {
            "tid": str(tenant_id),
            "sid": str(soul_id),
            "ct": ct,
            "nonce": nonce,
            "emb": embedding_str,
            "sal": salience,
            "tags": clean_tags,
            "flags": injection_flags,
            "client": source_client,
        },
    )

    flag_note = ""
    if injection_flags:
        flag_note = f" Warning: injection patterns detected: {injection_flags}."

    return f"Memory noted (pending review).{flag_note}"


async def recall(
    session: AsyncSession,
    soul_id: UUID,
    query: str,
    *,
    k: int = 5,
) -> str:
    """Semantic search over confirmed memories. Returns untrusted-wrapped results."""
    if len(query) > QUERY_MAX_LEN:
        return f"Error: query too long (max {QUERY_MAX_LEN} chars)."

    query_embedding = await embed_text(query)
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    dek = await _get_dek(session, soul_id)

    rows = await session.execute(
        text("""
            SELECT id, content_encrypted, content_nonce, created_at,
                   salience, tags, embedding <=> CAST(:qemb AS vector) AS distance
            FROM memories
            WHERE soul_id = :sid AND status = 'confirmed'
            ORDER BY embedding <=> CAST(:qemb AS vector)
            LIMIT :k
        """),
        {
            "sid": str(soul_id),
            "qemb": embedding_str,
            "k": k,
        },
    )
    results = rows.mappings().all()

    if not results:
        return "No matching memories found."

    parts = []
    memory_ids = []
    aad = build_aad(soul_id, "memory")
    for row in results:
        plaintext = decrypt_content(
            bytes(row["content_encrypted"]),
            bytes(row["content_nonce"]),
            dek,
            aad,
        )
        mem_id = str(row["id"])
        memory_ids.append(mem_id)
        created = row["created_at"].strftime("%Y-%m-%d")
        header = f"[{created}, salience={row['salience']:.1f}]"
        parts.append(wrap_untrusted(mem_id, f"{header}\n{plaintext}"))

    for mid in memory_ids:
        await session.execute(
            text("""
                UPDATE memories
                SET last_recalled_at = NOW(), recall_count = recall_count + 1
                WHERE id = :mid
            """),
            {"mid": mid},
        )

    return "\n\n".join(parts)


async def recall_recent(
    session: AsyncSession,
    soul_id: UUID,
    *,
    days: int = 7,
    limit: int = 10,
) -> str:
    """Return the most recent confirmed memories (chronological, no embedding needed)."""
    dek = await _get_dek(session, soul_id)
    cutoff = datetime.now(UTC) - timedelta(days=days)

    rows = await session.execute(
        text("""
            SELECT id, content_encrypted, content_nonce, created_at, salience, tags
            FROM memories
            WHERE soul_id = :sid AND status = 'confirmed' AND created_at >= :cutoff
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {
            "sid": str(soul_id),
            "cutoff": cutoff,
            "lim": limit,
        },
    )
    results = rows.mappings().all()

    if not results:
        return "No recent memories found."

    parts = []
    aad = build_aad(soul_id, "memory")
    for row in results:
        plaintext = decrypt_content(
            bytes(row["content_encrypted"]),
            bytes(row["content_nonce"]),
            dek,
            aad,
        )
        mem_id = str(row["id"])
        created = row["created_at"].strftime("%Y-%m-%d")
        header = f"[{created}, salience={row['salience']:.1f}]"
        parts.append(wrap_untrusted(mem_id, f"{header}\n{plaintext}"))

    return "\n\n".join(parts)
