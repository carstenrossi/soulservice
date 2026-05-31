"""Fact tools: learn_fact, get_facts, forget_fact."""

from __future__ import annotations

import re
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

VALUE_MAX_LEN = 4096
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")


def wrap_untrusted_fact(fact_id: str, content: str) -> str:
    """Wrap fact content in untrusted tags with output escaping."""
    escaped = content.replace("</retrieved_fact>", "&lt;/retrieved_fact&gt;")
    return (
        f'<retrieved_fact untrusted="true" id="{fact_id}">\n'
        f"{escaped}\n"
        f"</retrieved_fact>"
    )


def _validate_identifier(value: str, label: str) -> str:
    if not IDENTIFIER_PATTERN.match(value):
        msg = f"Invalid {label} format: '{value}'. Must match [a-z][a-z0-9_-]{{0,49}}."
        raise ValueError(msg)
    return value


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


async def learn_fact(
    session: AsyncSession,
    tenant_id: UUID,
    soul_id: UUID,
    category: str,
    key: str,
    value: str,
    *,
    confidence: float = 1.0,
) -> str:
    """Upsert a fact: insert or update if (category, key) already exists."""
    try:
        _validate_identifier(category, "category")
        _validate_identifier(key, "key")
    except ValueError as e:
        return f"Error: {e}"

    if len(value) > VALUE_MAX_LEN:
        return f"Error: value too long (max {VALUE_MAX_LEN} chars)."

    if not 0.0 <= confidence <= 1.0:
        return "Error: confidence must be between 0.0 and 1.0."

    dek = await _get_dek(session, soul_id)
    ct, nonce = encrypt_content(value, dek, build_aad(soul_id, "fact"))

    await session.execute(
        text("""
            INSERT INTO facts
                (tenant_id, soul_id, category, key, value_encrypted, value_nonce,
                 confidence, status, updated_at)
            VALUES
                (:tid, :sid, :cat, :key, :ct, :nonce, :conf, 'active', NOW())
            ON CONFLICT (tenant_id, soul_id, category, key)
            DO UPDATE SET
                value_encrypted = EXCLUDED.value_encrypted,
                value_nonce = EXCLUDED.value_nonce,
                confidence = EXCLUDED.confidence,
                status = 'active',
                updated_at = NOW()
        """),
        {
            "tid": str(tenant_id),
            "sid": str(soul_id),
            "cat": category,
            "key": key,
            "ct": ct,
            "nonce": nonce,
            "conf": confidence,
        },
    )

    return f"Fact noted: {category}/{key}."


async def get_facts(
    session: AsyncSession,
    soul_id: UUID,
    *,
    category: str | None = None,
) -> str:
    """Retrieve active facts, optionally filtered by category."""
    dek = await _get_dek(session, soul_id)

    if category:
        rows = await session.execute(
            text("""
                SELECT id, category, key, value_encrypted, value_nonce,
                       confidence, updated_at
                FROM facts
                WHERE soul_id = :sid AND status = 'active' AND category = :cat
                ORDER BY category, key
            """),
            {"sid": str(soul_id), "cat": category},
        )
    else:
        rows = await session.execute(
            text("""
                SELECT id, category, key, value_encrypted, value_nonce,
                       confidence, updated_at
                FROM facts
                WHERE soul_id = :sid AND status = 'active'
                ORDER BY category, key
            """),
            {"sid": str(soul_id)},
        )

    results = rows.mappings().all()

    if not results:
        filter_msg = f" in category '{category}'" if category else ""
        return f"No facts found{filter_msg}."

    parts = []
    aad = build_aad(soul_id, "fact")
    for row in results:
        plaintext = decrypt_content(
            bytes(row["value_encrypted"]),
            bytes(row["value_nonce"]),
            dek,
            aad,
        )
        fact_id = str(row["id"])
        updated = row["updated_at"].strftime("%Y-%m-%d")
        header = (
            f"[{row['category']}/{row['key']}, "
            f"confidence={row['confidence']:.1f}, updated={updated}]"
        )
        parts.append(wrap_untrusted_fact(fact_id, f"{header}\n{plaintext}"))

    return "\n\n".join(parts)


async def forget_fact(
    session: AsyncSession,
    soul_id: UUID,
    category: str,
    key: str,
) -> str:
    """Soft-delete a fact by setting status to 'deleted'."""
    try:
        _validate_identifier(category, "category")
        _validate_identifier(key, "key")
    except ValueError as e:
        return f"Error: {e}"

    row = await session.execute(
        text("""
            SELECT id FROM facts
            WHERE soul_id = :sid AND category = :cat AND key = :key
                  AND status = 'active'
        """),
        {"sid": str(soul_id), "cat": category, "key": key},
    )
    result = row.mappings().first()
    if result is None:
        return f"Error: no active fact '{category}/{key}' found."

    await session.execute(
        text("""
            UPDATE facts SET status = 'deleted', updated_at = NOW()
            WHERE id = :fid
        """),
        {"fid": str(result["id"])},
    )

    return f"Fact '{category}/{key}' forgotten."
