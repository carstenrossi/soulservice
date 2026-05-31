"""Structured DB queries for the web UI (admin/owner session)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import yaml
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.audit import log_tool_call
from soulservice.core.auth import VALID_MODES, generate_token
from soulservice.core.crypto import (
    build_aad,
    decrypt_content,
    decrypt_dek,
    dek_cache,
    encrypt_content,
)
from soulservice.core.embeddings import embed_text
from soulservice.mcp.tools.properties import (
    PROPERTY_SCHEMAS,
    delete_property,
    deserialize_value,
    set_property,
)
from soulservice.mcp.tools.review import decide_proposal

FACT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")

DECRYPTION_FAILED_PLACEHOLDER = "[decryption failed]"


def _safe_decrypt(ciphertext, nonce, dek: bytes, aad: bytes) -> tuple[str, bool]:
    """Decrypt one value, returning (text, failed).

    A single corrupt row must never take down a whole admin list view, so any
    decryption error degrades gracefully to a placeholder.
    """
    try:
        return (
            decrypt_content(bytes(ciphertext), bytes(nonce), dek, aad),
            False,
        )
    except Exception:
        return DECRYPTION_FAILED_PLACEHOLDER, True


async def list_souls(session: AsyncSession) -> list[dict]:
    rows = await session.execute(
        text("SELECT id, slug, display_name, status FROM souls ORDER BY display_name")
    )
    return [dict(r) for r in rows.mappings().all()]


async def resolve_soul(session: AsyncSession, slug: str) -> dict:
    row = await session.execute(
        text(
            "SELECT id, tenant_id, owner_user_id, slug, display_name "
            "FROM souls WHERE slug = :slug"
        ),
        {"slug": slug},
    )
    soul = row.mappings().first()
    if soul is None:
        raise HTTPException(status_code=404, detail=f"Soul '{slug}' not found")
    return dict(soul)


async def get_dek(session: AsyncSession, soul_id: UUID) -> bytes:
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


async def get_dashboard_data(session: AsyncSession, soul: dict) -> dict:
    soul_id = str(soul["id"])
    counts = {}

    count_queries = {
        "memories_confirmed": (
            "SELECT COUNT(*) FROM memories WHERE soul_id = :sid AND status = 'confirmed'"
        ),
        "memories_pending": (
            "SELECT COUNT(*) FROM memories WHERE soul_id = :sid AND status = 'pending'"
        ),
        "facts": "SELECT COUNT(*) FROM facts WHERE soul_id = :sid AND status = 'active'",
        "properties": (
            "SELECT COUNT(*) FROM soul_properties WHERE soul_id = :sid AND status = 'active'"
        ),
        "tokens_active": (
            "SELECT COUNT(*) FROM api_tokens WHERE soul_id = :sid AND revoked_at IS NULL"
        ),
    }
    for label, query in count_queries.items():
        row = await session.execute(text(query), {"sid": soul_id})
        counts[label] = row.scalar() or 0

    sc_row = await session.execute(
        text("SELECT current_version FROM soul_self_cores WHERE soul_id = :sid"),
        {"sid": soul_id},
    )
    sc = sc_row.mappings().first()
    counts["self_core_version"] = sc["current_version"] if sc else 0

    audit_rows = await session.execute(
        text(
            "SELECT occurred_at, tool_name, status, source_client "
            "FROM audit_log WHERE soul_id = :sid "
            "ORDER BY occurred_at DESC LIMIT 10"
        ),
        {"sid": soul_id},
    )
    recent_audit = [dict(r) for r in audit_rows.mappings().all()]

    return {"counts": counts, "recent_audit": recent_audit}


async def list_pending_proposals(session: AsyncSession, soul_id: UUID) -> list[dict]:
    dek = await get_dek(session, soul_id)
    aad = build_aad(soul_id, "memory")
    rows = await session.execute(
        text(
            "SELECT id, content_encrypted, content_nonce, created_at, salience, "
            "tags, injection_flags FROM memories "
            "WHERE soul_id = :sid AND status = 'pending' ORDER BY created_at DESC"
        ),
        {"sid": str(soul_id)},
    )
    items = []
    for m in rows.mappings().all():
        content, failed = _safe_decrypt(
            m["content_encrypted"], m["content_nonce"], dek, aad
        )
        items.append({
            "id": str(m["id"]),
            "content": content,
            "decryption_failed": failed,
            "created_at": m["created_at"],
            "salience": m["salience"],
            "tags": m["tags"] or [],
            "injection_flags": m["injection_flags"] or [],
        })
    return items


async def decide_proposal_web(
    session: AsyncSession, soul: dict, memory_id: str, action: str
) -> None:
    await decide_proposal(session, soul["id"], memory_id, action)
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul["id"],
        tool_name=f"web:decide_proposal:{action}",
        source_client="web-ui",
    )

async def list_memories(
    session: AsyncSession,
    soul_id: UUID,
    *,
    status: str = "confirmed",
    days: int = 30,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    dek = await get_dek(session, soul_id)
    aad = build_aad(soul_id, "memory")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = await session.execute(
        text(
            "SELECT id, content_encrypted, content_nonce, created_at, salience, tags, status "
            "FROM memories WHERE soul_id = :sid AND status = :st "
            "AND created_at >= :cutoff ORDER BY created_at DESC "
            "LIMIT :lim OFFSET :off"
        ),
        {
            "sid": str(soul_id),
            "st": status,
            "cutoff": cutoff,
            "lim": limit,
            "off": offset,
        },
    )
    items = []
    for m in rows.mappings().all():
        content, failed = _safe_decrypt(
            m["content_encrypted"], m["content_nonce"], dek, aad
        )
        items.append({
            "id": str(m["id"]),
            "content": content,
            "decryption_failed": failed,
            "created_at": m["created_at"],
            "salience": m["salience"],
            "tags": m["tags"] or [],
            "status": m["status"],
        })
    return items


async def search_memories(
    session: AsyncSession, soul_id: UUID, query: str, k: int = 10
) -> list[dict]:
    dek = await get_dek(session, soul_id)
    aad = build_aad(soul_id, "memory")
    query_embedding = await embed_text(query)
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
    rows = await session.execute(
        text(
            "SELECT id, content_encrypted, content_nonce, created_at, salience, tags, "
            "embedding <=> CAST(:qemb AS vector) AS distance "
            "FROM memories WHERE soul_id = :sid AND status = 'confirmed' "
            "ORDER BY embedding <=> CAST(:qemb AS vector) LIMIT :k"
        ),
        {"sid": str(soul_id), "qemb": embedding_str, "k": k},
    )
    items = []
    for m in rows.mappings().all():
        content, failed = _safe_decrypt(
            m["content_encrypted"], m["content_nonce"], dek, aad
        )
        items.append({
            "id": str(m["id"]),
            "content": content,
            "decryption_failed": failed,
            "created_at": m["created_at"],
            "salience": m["salience"],
            "tags": m["tags"] or [],
            "distance": m["distance"],
        })
    return items


async def get_memory(session: AsyncSession, soul_id: UUID, memory_id: str) -> dict | None:
    dek = await get_dek(session, soul_id)
    aad = build_aad(soul_id, "memory")
    row = await session.execute(
        text(
            "SELECT id, content_encrypted, content_nonce, created_at, salience, tags, status "
            "FROM memories WHERE id = :mid AND soul_id = :sid"
        ),
        {"mid": memory_id, "sid": str(soul_id)},
    )
    m = row.mappings().first()
    if m is None:
        return None
    content, failed = _safe_decrypt(
        m["content_encrypted"], m["content_nonce"], dek, aad
    )
    return {
        "id": str(m["id"]),
        "content": content,
        "decryption_failed": failed,
        "created_at": m["created_at"],
        "salience": m["salience"],
        "tags": m["tags"] or [],
        "status": m["status"],
    }


async def forget_memory_web(session: AsyncSession, soul: dict, memory_id: str) -> None:
    row = await session.execute(
        text("SELECT id, status FROM memories WHERE id = :mid AND soul_id = :sid"),
        {"mid": memory_id, "sid": str(soul["id"])},
    )
    result = row.mappings().first()
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if result["status"] not in ("confirmed", "pending"):
        raise HTTPException(status_code=400, detail=f"Memory is '{result['status']}'")

    await session.execute(
        text("UPDATE memories SET status = 'forgotten' WHERE id = :mid"),
        {"mid": memory_id},
    )
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul["id"],
        tool_name="web:forget_memory",
        source_client="web-ui",
    )

async def list_facts(
    session: AsyncSession, soul_id: UUID, category: str | None = None
) -> list[dict]:
    dek = await get_dek(session, soul_id)
    aad = build_aad(soul_id, "fact")
    base = (
        "SELECT id, category, key, value_encrypted, value_nonce, confidence, updated_at "
        "FROM facts WHERE soul_id = :sid AND status = 'active'"
    )
    params: dict = {"sid": str(soul_id)}
    if category:
        base += " AND category = :cat"
        params["cat"] = category
    base += " ORDER BY category, key"
    rows = await session.execute(text(base), params)
    items = []
    for f in rows.mappings().all():
        value, failed = _safe_decrypt(
            f["value_encrypted"], f["value_nonce"], dek, aad
        )
        items.append({
            "id": str(f["id"]),
            "category": f["category"],
            "key": f["key"],
            "value": value,
            "decryption_failed": failed,
            "confidence": f["confidence"],
            "updated_at": f["updated_at"],
        })
    return items


async def set_fact_web(
    session: AsyncSession,
    soul: dict,
    category: str,
    key: str,
    value: str,
    confidence: float = 1.0,
) -> None:
    if not FACT_PATTERN.match(category):
        raise HTTPException(status_code=400, detail=f"Invalid category: '{category}'")
    if not FACT_PATTERN.match(key):
        raise HTTPException(status_code=400, detail=f"Invalid key: '{key}'")

    soul_id = soul["id"]
    dek = await get_dek(session, soul_id)
    ct, nonce = encrypt_content(value, dek, build_aad(soul_id, "fact"))
    await session.execute(
        text("""
            INSERT INTO facts
                (tenant_id, soul_id, category, key, value_encrypted,
                 value_nonce, confidence, status, updated_at)
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
            "tid": str(soul["tenant_id"]),
            "sid": str(soul_id),
            "cat": category,
            "key": key,
            "ct": ct,
            "nonce": nonce,
            "conf": confidence,
        },
    )
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul_id,
        tool_name="web:set_fact",
        source_client="web-ui",
    )

async def remove_fact_web(
    session: AsyncSession, soul: dict, category: str, key: str
) -> None:
    row = await session.execute(
        text(
            "SELECT id FROM facts WHERE soul_id = :sid "
            "AND category = :cat AND key = :key AND status = 'active'"
        ),
        {"sid": str(soul["id"]), "cat": category, "key": key},
    )
    fact = row.mappings().first()
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")

    await session.execute(
        text("UPDATE facts SET status = 'deleted', updated_at = NOW() WHERE id = :fid"),
        {"fid": str(fact["id"])},
    )
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul["id"],
        tool_name="web:remove_fact",
        source_client="web-ui",
    )

async def list_properties(session: AsyncSession, soul_id: UUID) -> list[dict]:
    dek = await get_dek(session, soul_id)
    aad = build_aad(soul_id, "property")
    rows = await session.execute(
        text(
            "SELECT property_type, schema_version, value, is_sensitive, "
            "value_encrypted, value_nonce, updated_at "
            "FROM soul_properties WHERE soul_id = :sid AND status = 'active' "
            "ORDER BY property_type"
        ),
        {"sid": str(soul_id)},
    )
    items = []
    for prop in rows.mappings().all():
        try:
            value_dict = deserialize_value(
                prop, dek if prop["is_sensitive"] else None, aad
            )
            failed = False
        except Exception:
            value_dict = {"error": DECRYPTION_FAILED_PLACEHOLDER}
            failed = True
        items.append({
            "property_type": prop["property_type"],
            "schema_version": prop["schema_version"],
            "value": value_dict,
            "decryption_failed": failed,
            "is_sensitive": prop["is_sensitive"],
            "updated_at": prop["updated_at"],
        })
    return items


async def set_property_web(
    session: AsyncSession, soul: dict, property_type: str, value: dict
) -> str:
    msg = await set_property(
        session, soul["tenant_id"], soul["id"], property_type, value
    )
    if msg.startswith("Error:"):
        raise HTTPException(status_code=400, detail=msg.removeprefix("Error: ").strip())
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul["id"],
        tool_name="web:set_property",
        source_client="web-ui",
    )
    return msg


async def delete_property_web(session: AsyncSession, soul: dict, property_type: str) -> str:
    msg = await delete_property(session, soul["id"], property_type)
    if msg.startswith("Error:"):
        raise HTTPException(status_code=404, detail=msg.removeprefix("Error: ").strip())
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul["id"],
        tool_name="web:delete_property",
        source_client="web-ui",
    )
    return msg


async def load_self_core(session: AsyncSession, soul: dict) -> tuple[str, int]:
    soul_id = soul["id"]
    dek = await get_dek(session, soul_id)
    row = await session.execute(
        text(
            "SELECT content_encrypted, content_nonce, current_version "
            "FROM soul_self_cores WHERE soul_id = :sid"
        ),
        {"sid": str(soul_id)},
    )
    r = row.mappings().first()
    if r is None:
        return f"# Self Core for {soul['slug']}\n", 0
    content, _failed = _safe_decrypt(
        r["content_encrypted"],
        r["content_nonce"],
        dek,
        build_aad(soul_id, "self_core"),
    )
    return content, r["current_version"]


async def save_self_core(
    session: AsyncSession,
    soul: dict,
    new_yaml: str,
    note: str,
    expected_version: int,
) -> int:
    """Validate YAML, encrypt, archive prior version, bump version.

    ``expected_version`` is the version the editor loaded. If the stored version
    has moved on in the meantime, raise 409 so we never silently overwrite a
    concurrent edit (optimistic concurrency).
    """
    yaml.safe_load(new_yaml)
    soul_id = soul["id"]
    tenant_id = soul["tenant_id"]
    user_id = soul["owner_user_id"]
    dek = await get_dek(session, soul_id)
    ct, nonce = encrypt_content(new_yaml, dek, build_aad(soul_id, "self_core"))

    row = await session.execute(
        text("SELECT current_version FROM soul_self_cores WHERE soul_id = :sid"),
        {"sid": str(soul_id)},
    )
    existing = row.mappings().first()
    current_version = existing["current_version"] if existing else 0
    if expected_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=(
                "Self Core was modified since you loaded it "
                f"(now v{current_version}). Reload and reapply your changes."
            ),
        )
    if existing is None:
        new_version = 1
        await session.execute(
            text(
                "INSERT INTO soul_self_cores "
                "(soul_id, tenant_id, content_encrypted, content_nonce, "
                "current_version, updated_by) "
                "VALUES (:sid, :tid, :ct, :nonce, 1, :uid)"
            ),
            {
                "sid": str(soul_id),
                "tid": str(tenant_id),
                "ct": ct,
                "nonce": nonce,
                "uid": str(user_id),
            },
        )
    else:
        new_version = existing["current_version"] + 1
        await session.execute(
            text(
                "INSERT INTO soul_self_core_history "
                "(soul_id, tenant_id, version, content_encrypted, content_nonce, "
                "changed_by, change_note) "
                "SELECT soul_id, tenant_id, current_version, content_encrypted, "
                "content_nonce, updated_by, :note "
                "FROM soul_self_cores WHERE soul_id = :sid"
            ),
            {"sid": str(soul_id), "note": note or None},
        )
        await session.execute(
            text(
                "UPDATE soul_self_cores SET content_encrypted = :ct, "
                "content_nonce = :nonce, current_version = :ver, "
                "updated_at = NOW(), updated_by = :uid WHERE soul_id = :sid"
            ),
            {
                "ct": ct,
                "nonce": nonce,
                "ver": new_version,
                "uid": str(user_id),
                "sid": str(soul_id),
            },
        )
    await log_tool_call(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        soul_id=soul_id,
        tool_name="web:save_self_core",
        source_client="web-ui",
    )
    return new_version


async def list_tokens(session: AsyncSession, soul_id: UUID) -> list[dict]:
    rows = await session.execute(
        text(
            "SELECT id, token_prefix, name, scopes, mode, created_at, "
            "last_used_at, expires_at, revoked_at "
            "FROM api_tokens WHERE soul_id = :sid ORDER BY created_at DESC"
        ),
        {"sid": str(soul_id)},
    )
    items = []
    for t in rows.mappings().all():
        items.append({
            "id": str(t["id"]),
            "token_prefix": t["token_prefix"],
            "name": t["name"],
            "scopes": list(t["scopes"]) if t["scopes"] else [],
            "mode": t.get("mode", "identity") or "identity",
            "created_at": t["created_at"],
            "last_used_at": t["last_used_at"],
            "expires_at": t["expires_at"],
            "revoked": t["revoked_at"] is not None,
        })
    return items


async def create_token_web(
    session: AsyncSession,
    soul: dict,
    name: str,
    *,
    mode: str = "identity",
    read_only: bool = False,
    expires_days: int = 90,
) -> tuple[str, dict]:
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}")

    scopes = ["read"] if read_only else ["read", "write"]
    full_token, prefix, token_hash = generate_token("dev")
    expires_at = datetime.now(UTC) + timedelta(days=expires_days)

    await session.execute(
        text(
            "INSERT INTO api_tokens "
            "(tenant_id, user_id, soul_id, token_hash, token_prefix, "
            "name, scopes, mode, expires_at) "
            "VALUES (:tid, :uid, :sid, :hash, :prefix, :name, :scopes, :mode, :exp) "
            "RETURNING id, created_at"
        ),
        {
            "tid": str(soul["tenant_id"]),
            "uid": str(soul["owner_user_id"]),
            "sid": str(soul["id"]),
            "hash": token_hash,
            "prefix": prefix,
            "name": name,
            "scopes": scopes,
            "mode": mode,
            "exp": expires_at,
        },
    )
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        user_id=soul["owner_user_id"],
        soul_id=soul["id"],
        tool_name="web:create_token",
        source_client="web-ui",
    )
    meta = {
        "name": name,
        "mode": mode,
        "scopes": scopes,
        "expires_at": expires_at,
    }
    return full_token, meta


async def revoke_token_web(session: AsyncSession, soul: dict, token_id: str) -> None:
    row = await session.execute(
        text("SELECT id FROM api_tokens WHERE id = :tid AND soul_id = :sid"),
        {"tid": token_id, "sid": str(soul["id"])},
    )
    if row.mappings().first() is None:
        raise HTTPException(status_code=404, detail="Token not found")

    await session.execute(
        text("UPDATE api_tokens SET revoked_at = NOW() WHERE id = :tid"),
        {"tid": token_id},
    )
    await log_tool_call(
        session,
        tenant_id=soul["tenant_id"],
        soul_id=soul["id"],
        tool_name="web:revoke_token",
        source_client="web-ui",
    )

async def list_audit(
    session: AsyncSession,
    soul_id: UUID,
    *,
    tool_name: str | None = None,
    days: int = 7,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    base = (
        "SELECT occurred_at, tool_name, status, source_client, result_size, args_hash "
        "FROM audit_log WHERE soul_id = :sid AND occurred_at >= :cutoff"
    )
    params: dict = {"sid": str(soul_id), "cutoff": cutoff, "lim": limit, "off": offset}
    if tool_name:
        base += " AND tool_name ILIKE :tool"
        params["tool"] = f"%{tool_name}%"
    base += " ORDER BY occurred_at DESC LIMIT :lim OFFSET :off"
    rows = await session.execute(text(base), params)
    return [dict(r) for r in rows.mappings().all()]


def property_schemas_for_templates() -> dict:
    """Expose PROPERTY_SCHEMAS for Jinja templates."""
    return PROPERTY_SCHEMAS
