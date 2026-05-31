"""Soul export/import: portable decrypted bundles with re-encryption on import."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.crypto import (
    build_aad,
    decrypt_content,
    decrypt_dek,
    encrypt_content,
    encrypt_dek,
    generate_dek,
)
from soulservice.core.embeddings import EMBEDDING_DIM, embed_text
from soulservice.mcp.tools.properties import PROPERTY_SCHEMAS, deserialize_value, set_property

EXPORT_SCHEMA_VERSION = 1
VALID_CONFLICT_MODES = ("overwrite", "skip")


def format_embedding(vec: list[float]) -> str:
    """Format a float vector for pgvector CAST(:emb AS vector)."""
    return "[" + ",".join(str(v) for v in vec) + "]"


def memory_to_ndjson_line(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False)


def parse_ndjson_line(line: str) -> dict:
    return json.loads(line)


def validate_manifest(manifest: dict) -> None:
    version = manifest.get("schema_version")
    if version != EXPORT_SCHEMA_VERSION:
        msg = f"Unsupported export schema_version: {version!r}"
        raise ValueError(msg)
    if "source" not in manifest:
        msg = "Manifest missing required key: source"
        raise ValueError(msg)
    if "self_core" not in manifest:
        msg = "Manifest missing required key: self_core"
        raise ValueError(msg)


def validate_conflict_mode(mode: str) -> str:
    if mode not in VALID_CONFLICT_MODES:
        msg = f"Invalid on_conflict mode: {mode!r}. Must be one of {VALID_CONFLICT_MODES}."
        raise ValueError(msg)
    return mode


def build_manifest(
    source: dict,
    self_core: dict,
    facts: list[dict],
    properties: list[dict],
    adaptations: list[dict],
    audit: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "source": source,
        "self_core": self_core,
        "facts": facts,
        "properties": properties,
        "adaptations": adaptations,
        "audit": audit if audit is not None else [],
    }


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


async def _load_dek(session: AsyncSession, soul_id: UUID) -> bytes:
    row = await session.execute(
        text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
        {"sid": str(soul_id)},
    )
    result = row.mappings().first()
    if result is None:
        msg = f"No encryption key for soul {soul_id}"
        raise ValueError(msg)
    return decrypt_dek(bytes(result["dek_encrypted"]), build_aad(soul_id, "dek"))


def _normalize_embedding(raw) -> list[float]:
    if raw is None:
        return []
    if isinstance(raw, str):
        inner = raw.strip().lstrip("[").rstrip("]")
        if not inner:
            return []
        return [float(x) for x in inner.split(",")]
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    return []


async def export_soul(
    session: AsyncSession,
    soul_slug: str,
    *,
    include_audit: bool = False,
    all_statuses: bool = False,
) -> tuple[dict, list[dict]]:
    """Export a soul as (manifest, memories). Does not commit."""
    row = await session.execute(
        text(
            "SELECT id, tenant_id, owner_user_id, slug, display_name, status "
            "FROM souls WHERE slug = :slug"
        ),
        {"slug": soul_slug},
    )
    soul = row.mappings().first()
    if soul is None:
        msg = f"Soul '{soul_slug}' not found"
        raise ValueError(msg)

    soul_id: UUID = soul["id"]
    dek = await _load_dek(session, soul_id)

    source = {
        "soul_id": str(soul_id),
        "tenant_id": str(soul["tenant_id"]),
        "owner_user_id": str(soul["owner_user_id"]),
        "slug": soul["slug"],
        "display_name": soul["display_name"],
        "status": soul["status"],
    }

    sc_row = await session.execute(
        text(
            "SELECT content_encrypted, content_nonce, current_version "
            "FROM soul_self_cores WHERE soul_id = :sid"
        ),
        {"sid": str(soul_id)},
    )
    sc = sc_row.mappings().first()
    if sc:
        content = decrypt_content(
            bytes(sc["content_encrypted"]),
            bytes(sc["content_nonce"]),
            dek,
            build_aad(soul_id, "self_core"),
        )
        self_core = {
            "current_version": sc["current_version"],
            "content": content,
            "history": [],
        }
    else:
        self_core = {"current_version": 0, "content": None, "history": []}

    hist_rows = await session.execute(
        text(
            "SELECT version, content_encrypted, content_nonce, changed_at, change_note "
            "FROM soul_self_core_history WHERE soul_id = :sid ORDER BY version"
        ),
        {"sid": str(soul_id)},
    )
    for h in hist_rows.mappings().all():
        hist_content = decrypt_content(
            bytes(h["content_encrypted"]),
            bytes(h["content_nonce"]),
            dek,
            build_aad(soul_id, "self_core"),
        )
        self_core["history"].append(
            {
                "version": h["version"],
                "content": hist_content,
                "changed_at": _iso(h["changed_at"]),
                "change_note": h["change_note"],
            }
        )

    fact_sql = (
        "SELECT category, key, value_encrypted, value_nonce, confidence, status, updated_at "
        "FROM facts WHERE soul_id = :sid"
    )
    if not all_statuses:
        fact_sql += " AND status = 'active'"
    fact_sql += " ORDER BY category, key"
    fact_rows = await session.execute(text(fact_sql), {"sid": str(soul_id)})
    facts = []
    for f in fact_rows.mappings().all():
        value = decrypt_content(
            bytes(f["value_encrypted"]),
            bytes(f["value_nonce"]),
            dek,
            build_aad(soul_id, "fact"),
        )
        facts.append(
            {
                "category": f["category"],
                "key": f["key"],
                "value": value,
                "confidence": f["confidence"],
                "status": f["status"],
                "updated_at": _iso(f["updated_at"]),
            }
        )

    prop_rows = await session.execute(
        text(
            "SELECT property_type, schema_version, value, is_sensitive, "
            "value_encrypted, value_nonce, status, updated_at "
            "FROM soul_properties WHERE soul_id = :sid AND status = 'active' "
            "ORDER BY property_type"
        ),
        {"sid": str(soul_id)},
    )
    properties = []
    prop_aad = build_aad(soul_id, "property")
    for prop in prop_rows.mappings().all():
        value_dict = deserialize_value(
            prop, dek if prop["is_sensitive"] else None, prop_aad
        )
        properties.append(
            {
                "property_type": prop["property_type"],
                "schema_version": prop["schema_version"],
                "value": value_dict,
                "is_sensitive": prop["is_sensitive"],
                "status": prop["status"],
                "updated_at": _iso(prop["updated_at"]),
            }
        )

    adapt_sql = (
        "SELECT category, content_encrypted, content_nonce, confidence, source, "
        "status, created_at FROM soul_adaptations WHERE soul_id = :sid"
    )
    if not all_statuses:
        adapt_sql += " AND status = 'active'"
    adapt_sql += " ORDER BY created_at"
    adapt_rows = await session.execute(text(adapt_sql), {"sid": str(soul_id)})
    adaptations = []
    for a in adapt_rows.mappings().all():
        content = decrypt_content(
            bytes(a["content_encrypted"]),
            bytes(a["content_nonce"]),
            dek,
            build_aad(soul_id, "adaptation"),
        )
        adaptations.append(
            {
                "category": a["category"],
                "content": content,
                "confidence": a["confidence"],
                "source": a["source"],
                "status": a["status"],
                "created_at": _iso(a["created_at"]),
            }
        )

    mem_sql = (
        "SELECT content_encrypted, content_nonce, embedding, created_at, "
        "last_recalled_at, recall_count, source_client, salience, status, tags, "
        "injection_flags FROM memories WHERE soul_id = :sid"
    )
    if all_statuses:
        mem_sql += " ORDER BY created_at"
    else:
        mem_sql += " AND status IN ('confirmed', 'pending') ORDER BY created_at"
    mem_rows = await session.execute(text(mem_sql), {"sid": str(soul_id)})
    memories = []
    mem_aad = build_aad(soul_id, "memory")
    for m in mem_rows.mappings().all():
        content = decrypt_content(
            bytes(m["content_encrypted"]),
            bytes(m["content_nonce"]),
            dek,
            mem_aad,
        )
        memories.append(
            {
                "content": content,
                "salience": m["salience"],
                "status": m["status"],
                "tags": list(m["tags"] or []),
                "injection_flags": list(m["injection_flags"] or []),
                "source_client": m["source_client"],
                "created_at": _iso(m["created_at"]),
                "last_recalled_at": _iso(m["last_recalled_at"]),
                "recall_count": m["recall_count"],
                "embedding": _normalize_embedding(m["embedding"]),
            }
        )

    audit: list[dict] = []
    if include_audit:
        audit_rows = await session.execute(
            text(
                "SELECT occurred_at, tool_name, status, source_client, result_size, "
                "args_hash FROM audit_log WHERE soul_id = :sid ORDER BY occurred_at"
            ),
            {"sid": str(soul_id)},
        )
        for a in audit_rows.mappings().all():
            audit.append(
                {
                    "occurred_at": _iso(a["occurred_at"]),
                    "tool_name": a["tool_name"],
                    "status": a["status"],
                    "source_client": a["source_client"],
                    "result_size": a["result_size"],
                    "args_hash": a["args_hash"],
                }
            )

    manifest = build_manifest(source, self_core, facts, properties, adaptations, audit)
    return manifest, memories


async def import_soul(
    session: AsyncSession,
    manifest: dict,
    memories: list[dict],
    *,
    into_slug: str | None = None,
    owner_user_id: str | None = None,
    new_slug: str | None = None,
    display_name: str | None = None,
    on_conflict: str = "overwrite",
    recompute_embeddings: bool = False,
) -> dict:
    """Import a soul bundle. Does not commit."""
    validate_manifest(manifest)
    validate_conflict_mode(on_conflict)

    created_new = False
    resolved_owner_user_id: str | None = owner_user_id

    if into_slug:
        row = await session.execute(
            text("SELECT id, tenant_id, owner_user_id FROM souls WHERE slug = :slug"),
            {"slug": into_slug},
        )
        target = row.mappings().first()
        if target is None:
            msg = f"Soul '{into_slug}' not found"
            raise ValueError(msg)
        new_soul_id: UUID = target["id"]
        tenant_id = target["tenant_id"]
        resolved_owner_user_id = str(target["owner_user_id"])
        dek = await _load_dek(session, new_soul_id)
    elif owner_user_id:
        user_row = await session.execute(
            text("SELECT tenant_id FROM users WHERE id = :uid"),
            {"uid": owner_user_id},
        )
        user = user_row.mappings().first()
        if user is None:
            msg = f"User '{owner_user_id}' not found"
            raise ValueError(msg)
        tenant_id = user["tenant_id"]
        slug = new_slug or manifest["source"]["slug"]
        display = display_name or manifest["source"]["display_name"]
        result = await session.execute(
            text(
                "INSERT INTO souls (tenant_id, owner_user_id, slug, display_name) "
                "VALUES (:tid, :uid, :slug, :display) RETURNING id"
            ),
            {
                "tid": str(tenant_id),
                "uid": owner_user_id,
                "slug": slug,
                "display": display,
            },
        )
        new_soul_id = result.mappings().first()["id"]
        dek = generate_dek()
        dek_enc = encrypt_dek(dek, build_aad(new_soul_id, "dek"))
        await session.execute(
            text(
                "INSERT INTO soul_keys (soul_id, tenant_id, dek_encrypted) "
                "VALUES (:sid, :tid, :dek)"
            ),
            {"sid": str(new_soul_id), "tid": str(tenant_id), "dek": dek_enc},
        )
        created_new = True
    else:
        msg = "Provide either into_slug (merge) or owner_user_id (new soul)."
        raise ValueError(msg)

    stats = {
        "soul_id": str(new_soul_id),
        "created_new": created_new,
        "self_core": 0,
        "memories": 0,
        "facts": 0,
        "properties": 0,
        "adaptations": 0,
        "skipped_properties": 0,
    }

    self_core_data = manifest.get("self_core") or {}
    sc_content = self_core_data.get("content")
    if sc_content:
        ct, nonce = encrypt_content(sc_content, dek, build_aad(new_soul_id, "self_core"))
        existing = await session.execute(
            text("SELECT current_version FROM soul_self_cores WHERE soul_id = :sid"),
            {"sid": str(new_soul_id)},
        )
        existing_row = existing.mappings().first()
        uid = resolved_owner_user_id

        if existing_row is None:
            await session.execute(
                text(
                    "INSERT INTO soul_self_cores "
                    "(soul_id, tenant_id, content_encrypted, content_nonce, "
                    "current_version, updated_by) "
                    "VALUES (:sid, :tid, :ct, :nonce, 1, :uid)"
                ),
                {
                    "sid": str(new_soul_id),
                    "tid": str(tenant_id),
                    "ct": ct,
                    "nonce": nonce,
                    "uid": uid,
                },
            )
            stats["self_core"] = 1
            for hist in self_core_data.get("history") or []:
                hct, hnonce = encrypt_content(
                    hist["content"], dek, build_aad(new_soul_id, "self_core")
                )
                await session.execute(
                    text(
                        "INSERT INTO soul_self_core_history "
                        "(soul_id, tenant_id, version, content_encrypted, content_nonce, "
                        "changed_by, change_note) "
                        "VALUES (:sid, :tid, :ver, :ct, :nonce, :uid, :note)"
                    ),
                    {
                        "sid": str(new_soul_id),
                        "tid": str(tenant_id),
                        "ver": hist["version"],
                        "ct": hct,
                        "nonce": hnonce,
                        "uid": uid,
                        "note": hist.get("change_note"),
                    },
                )
        else:
            await session.execute(
                text(
                    "INSERT INTO soul_self_core_history "
                    "(soul_id, tenant_id, version, content_encrypted, content_nonce, "
                    "changed_by, change_note) "
                    "SELECT soul_id, tenant_id, current_version, content_encrypted, "
                    "content_nonce, updated_by, :note "
                    "FROM soul_self_cores WHERE soul_id = :sid"
                ),
                {"sid": str(new_soul_id), "note": "import merge"},
            )
            new_version = existing_row["current_version"] + 1
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
                    "uid": uid,
                    "sid": str(new_soul_id),
                },
            )
            stats["self_core"] = 1

    fact_upsert = """
        INSERT INTO facts
            (tenant_id, soul_id, category, key, value_encrypted,
             value_nonce, confidence, status, updated_at)
        VALUES
            (:tid, :sid, :cat, :key, :ct, :nonce, :conf, :st, NOW())
        ON CONFLICT (tenant_id, soul_id, category, key)
        DO UPDATE SET
            value_encrypted = EXCLUDED.value_encrypted,
            value_nonce = EXCLUDED.value_nonce,
            confidence = EXCLUDED.confidence,
            status = EXCLUDED.status,
            updated_at = NOW()
    """
    fact_skip = """
        INSERT INTO facts
            (tenant_id, soul_id, category, key, value_encrypted,
             value_nonce, confidence, status, updated_at)
        VALUES
            (:tid, :sid, :cat, :key, :ct, :nonce, :conf, :st, NOW())
        ON CONFLICT (tenant_id, soul_id, category, key) DO NOTHING
    """
    for fact in manifest.get("facts") or []:
        ct, nonce = encrypt_content(
            fact["value"], dek, build_aad(new_soul_id, "fact")
        )
        params = {
            "tid": str(tenant_id),
            "sid": str(new_soul_id),
            "cat": fact["category"],
            "key": fact["key"],
            "ct": ct,
            "nonce": nonce,
            "conf": fact.get("confidence", 1.0),
            "st": fact.get("status", "active"),
        }
        sql = fact_skip if on_conflict == "skip" else fact_upsert
        await session.execute(text(sql), params)
        stats["facts"] += 1

    for prop in manifest.get("properties") or []:
        ptype = prop["property_type"]
        if ptype not in PROPERTY_SCHEMAS:
            stats["skipped_properties"] += 1
            continue
        if on_conflict == "skip":
            existing = await session.execute(
                text(
                    "SELECT id FROM soul_properties "
                    "WHERE soul_id = :sid AND property_type = :ptype AND status = 'active'"
                ),
                {"sid": str(new_soul_id), "ptype": ptype},
            )
            if existing.mappings().first() is not None:
                stats["skipped_properties"] += 1
                continue
        msg = await set_property(
            session, tenant_id, new_soul_id, ptype, prop["value"]
        )
        if msg.startswith("Error:"):
            stats["skipped_properties"] += 1
        else:
            stats["properties"] += 1

    for adapt in manifest.get("adaptations") or []:
        ct, nonce = encrypt_content(
            adapt["content"], dek, build_aad(new_soul_id, "adaptation")
        )
        await session.execute(
            text(
                "INSERT INTO soul_adaptations "
                "(tenant_id, soul_id, category, content_encrypted, content_nonce, "
                "confidence, source, status) "
                "VALUES (:tid, :sid, :cat, :ct, :nonce, :conf, :src, :st)"
            ),
            {
                "tid": str(tenant_id),
                "sid": str(new_soul_id),
                "cat": adapt["category"],
                "ct": ct,
                "nonce": nonce,
                "conf": adapt.get("confidence", 0.5),
                "src": adapt.get("source"),
                "st": adapt.get("status", "active"),
            },
        )
        stats["adaptations"] += 1

    for mem in memories:
        content = mem["content"]
        embedding = _normalize_embedding(mem.get("embedding"))
        if (
            recompute_embeddings
            or not embedding
            or len(embedding) != EMBEDDING_DIM
        ):
            embedding = await embed_text(content)
        emb_str = format_embedding(embedding)
        ct, nonce = encrypt_content(content, dek, build_aad(new_soul_id, "memory"))
        await session.execute(
            text(
                "INSERT INTO memories "
                "(tenant_id, soul_id, content_encrypted, content_nonce, embedding, "
                "salience, status, tags, injection_flags, source_client) "
                "VALUES "
                "(:tid, :sid, :ct, :nonce, CAST(:emb AS vector), :sal, :st, "
                ":tags, :flags, :client)"
            ),
            {
                "tid": str(tenant_id),
                "sid": str(new_soul_id),
                "ct": ct,
                "nonce": nonce,
                "emb": emb_str,
                "sal": mem.get("salience", 0.5),
                "st": mem.get("status", "confirmed"),
                "tags": mem.get("tags") or [],
                "flags": mem.get("injection_flags") or [],
                "client": mem.get("source_client"),
            },
        )
        stats["memories"] += 1

    return stats
