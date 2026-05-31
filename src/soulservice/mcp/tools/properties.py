"""Property tools: set_property, get_properties, delete_property.

Soul properties are typed, structured JSON objects (one row per property_type).
Non-sensitive properties are stored as queryable JSONB; sensitive ones are
AES-256-GCM encrypted, and the `value` column holds only a redacted placeholder.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.crypto import (
    decrypt_content,
    decrypt_dek,
    dek_cache,
    encrypt_content,
)

VALUE_MAX_LEN = 8192  # max length of the JSON-serialized value
PROPERTY_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,49}$")

# Registry of known property types: schema version, sensitivity, allowed keys.
PROPERTY_SCHEMAS: dict[str, dict] = {
    "communication_style": {
        "version": 1,
        "sensitive": False,
        "allowed_keys": {"formality", "verbosity", "humor", "notes"},
    },
    "interests": {
        "version": 1,
        "sensitive": False,
        "allowed_keys": {"topics", "notes"},
    },
    "locale": {
        "version": 1,
        "sensitive": False,
        "allowed_keys": {"timezone", "language", "region"},
    },
    "boundaries": {
        "version": 1,
        "sensitive": True,
        "allowed_keys": {"avoid_topics", "hard_limits", "notes"},
    },
}


def wrap_untrusted_property(property_type: str, content: str) -> str:
    """Wrap property content in untrusted tags with output escaping."""
    escaped = content.replace("</retrieved_property>", "&lt;/retrieved_property&gt;")
    return (
        f'<retrieved_property untrusted="true" type="{property_type}">\n'
        f"{escaped}\n"
        f"</retrieved_property>"
    )


def _validate_property_type(value: str) -> str:
    if not PROPERTY_TYPE_PATTERN.match(value):
        msg = f"Invalid property_type: '{value}'. Must match [a-z][a-z0-9_]{{0,49}}."
        raise ValueError(msg)
    return value


def _validate_value(property_type: str, value: dict) -> None:
    if not isinstance(value, dict):
        msg = "Property value must be a JSON object."
        raise ValueError(msg)
    allowed = PROPERTY_SCHEMAS[property_type]["allowed_keys"]
    unknown = set(value) - allowed
    if unknown:
        msg = f"Unknown keys for '{property_type}': {', '.join(sorted(unknown))}."
        raise ValueError(msg)
    if len(json.dumps(value)) > VALUE_MAX_LEN:
        msg = f"Property value too large (max {VALUE_MAX_LEN} bytes serialized)."
        raise ValueError(msg)


def serialize_value(
    value: dict, is_sensitive: bool, dek: bytes | None
) -> tuple[dict, bytes | None, bytes | None]:
    """Return (jsonb_to_store, value_encrypted, value_nonce)."""
    if is_sensitive:
        if dek is None:
            msg = "DEK required for sensitive property."
            raise ValueError(msg)
        ct, nonce = encrypt_content(json.dumps(value), dek)
        return {"_encrypted": True}, ct, nonce
    return value, None, None


def deserialize_value(row, dek: bytes | None) -> dict:
    """Return the plaintext dict for a property row."""
    if row["is_sensitive"]:
        if dek is None:
            msg = "DEK required to decrypt sensitive property."
            raise ValueError(msg)
        plaintext = decrypt_content(
            bytes(row["value_encrypted"]), bytes(row["value_nonce"]), dek
        )
        return json.loads(plaintext)
    raw = row["value"]
    # asyncpg may return JSONB as str when the column is untyped in text() SQL.
    return json.loads(raw) if isinstance(raw, str) else raw


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
    dek = decrypt_dek(bytes(result["dek_encrypted"]))
    dek_cache.put(soul_id, dek)
    return dek


async def set_property(
    session: AsyncSession,
    tenant_id: UUID,
    soul_id: UUID,
    property_type: str,
    value: dict,
) -> str:
    """Upsert a typed property. Sensitivity comes from the schema registry."""
    try:
        _validate_property_type(property_type)
        if property_type not in PROPERTY_SCHEMAS:
            known = ", ".join(sorted(PROPERTY_SCHEMAS))
            msg = f"Unknown property_type '{property_type}'. Known: {known}."
            raise ValueError(msg)
        _validate_value(property_type, value)
    except ValueError as e:
        return f"Error: {e}"

    schema = PROPERTY_SCHEMAS[property_type]
    is_sensitive = schema["sensitive"]
    dek = await _get_dek(session, soul_id) if is_sensitive else None
    stored_value, ct, nonce = serialize_value(value, is_sensitive, dek)

    await session.execute(
        text("""
            INSERT INTO soul_properties
                (tenant_id, soul_id, property_type, schema_version, value,
                 is_sensitive, value_encrypted, value_nonce, status, updated_at)
            VALUES
                (:tid, :sid, :ptype, :ver, CAST(:val AS jsonb),
                 :sens, :ct, :nonce, 'active', NOW())
            ON CONFLICT (tenant_id, soul_id, property_type)
            DO UPDATE SET
                schema_version = EXCLUDED.schema_version,
                value = EXCLUDED.value,
                is_sensitive = EXCLUDED.is_sensitive,
                value_encrypted = EXCLUDED.value_encrypted,
                value_nonce = EXCLUDED.value_nonce,
                status = 'active',
                updated_at = NOW()
        """),
        {
            "tid": str(tenant_id),
            "sid": str(soul_id),
            "ptype": property_type,
            "ver": schema["version"],
            "val": json.dumps(stored_value),
            "sens": is_sensitive,
            "ct": ct,
            "nonce": nonce,
        },
    )
    return f"Property set: {property_type}."


async def get_properties(
    session: AsyncSession,
    soul_id: UUID,
    *,
    property_type: str | None = None,
) -> str:
    """Retrieve active properties, optionally filtered by type."""
    base = (
        "SELECT property_type, schema_version, value, is_sensitive, "
        "value_encrypted, value_nonce, updated_at "
        "FROM soul_properties WHERE soul_id = :sid AND status = 'active'"
    )
    params: dict = {"sid": str(soul_id)}
    if property_type:
        base += " AND property_type = :ptype"
        params["ptype"] = property_type
    base += " ORDER BY property_type"

    rows = await session.execute(text(base), params)
    results = rows.mappings().all()
    if not results:
        filter_msg = f" of type '{property_type}'" if property_type else ""
        return f"No properties found{filter_msg}."

    dek: bytes | None = None
    parts = []
    for row in results:
        if row["is_sensitive"] and dek is None:
            dek = await _get_dek(session, soul_id)
        value_dict = deserialize_value(row, dek)
        updated = row["updated_at"].strftime("%Y-%m-%d")
        header = (
            f"[{row['property_type']}, v{row['schema_version']}, updated={updated}]"
        )
        body = json.dumps(value_dict, ensure_ascii=False, indent=2)
        parts.append(wrap_untrusted_property(row["property_type"], f"{header}\n{body}"))

    return "\n\n".join(parts)


async def delete_property(
    session: AsyncSession,
    soul_id: UUID,
    property_type: str,
) -> str:
    """Soft-delete a property by setting status to 'deleted'."""
    try:
        _validate_property_type(property_type)
    except ValueError as e:
        return f"Error: {e}"

    row = await session.execute(
        text("""
            SELECT id FROM soul_properties
            WHERE soul_id = :sid AND property_type = :ptype AND status = 'active'
        """),
        {"sid": str(soul_id), "ptype": property_type},
    )
    result = row.mappings().first()
    if result is None:
        return f"Error: no active property '{property_type}' found."

    await session.execute(
        text("""
            UPDATE soul_properties SET status = 'deleted', updated_at = NOW()
            WHERE id = :pid
        """),
        {"pid": str(result["id"])},
    )
    return f"Property '{property_type}' deleted."
