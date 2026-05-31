"""Identity tools: who_are_you, whats_our_history."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soulservice.core.crypto import build_aad, decrypt_content, decrypt_dek, dek_cache
from soulservice.mcp.tools.memory import wrap_untrusted

# How many recent memories whats_our_history() surfaces as a warm-up baseline.
# Topic-specific recollection is the job of recall(query), not this overview.
HISTORY_RECENT_LIMIT = 8

MESSENGER_SELF_CORE_PREFIX = """\
Below is the personality profile of a Soul named {soul_name}.
The user is talking to you because they want to hear from this Soul.
Channel the Soul's voice as closely as you can -- use its speech
patterns, values, and style. Think of yourself as lending your voice
to this character. Speak AS the Soul, not ABOUT the Soul.

---

"""

MESSENGER_HISTORY_PREFIX = (
    "Relationship context for the Soul named {soul_name} with this user:\n\n"
)


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

    dek = decrypt_dek(bytes(result["dek_encrypted"]), build_aad(soul_id, "dek"))
    dek_cache.put(soul_id, dek)
    return dek


async def _get_soul_display_name(session: AsyncSession, soul_id: UUID) -> str:
    """Look up the display name for a soul (used in messenger-mode framing)."""
    row = await session.execute(
        text("SELECT display_name FROM souls WHERE id = :sid"),
        {"sid": str(soul_id)},
    )
    result = row.mappings().first()
    return result["display_name"] if result else "Unknown"


async def get_self_core(
    session: AsyncSession, soul_id: UUID, *, mode: str = "identity"
) -> str:
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

    content = decrypt_content(
        bytes(result["content_encrypted"]),
        bytes(result["content_nonce"]),
        dek,
        build_aad(soul_id, "self_core"),
    )

    adaptations = await _get_active_adaptations(session, soul_id, dek)
    if adaptations:
        content = content.rstrip() + "\n\n" + _format_adaptations(adaptations)

    if mode == "messenger":
        soul_name = await _get_soul_display_name(session, soul_id)
        return MESSENGER_SELF_CORE_PREFIX.format(soul_name=soul_name) + content

    return content


async def get_relationship_overview(
    session: AsyncSession, soul_id: UUID, *, mode: str = "identity"
) -> str:
    """Build a relationship overview from the soul's confirmed memories.

    Returns a chronological warm-up baseline (the most recent memories plus a
    total count) so the soul starts a session aware of its shared history.
    For topic-specific recollection the soul should call recall(query); this
    overview deliberately stays chronological, not semantic.
    """
    dek = await _get_dek(session, soul_id)

    total = (
        await session.execute(
            text(
                "SELECT count(*) FROM memories "
                "WHERE soul_id = :sid AND status = 'confirmed'"
            ),
            {"sid": str(soul_id)},
        )
    ).scalar() or 0

    if total == 0:
        # No shared history yet — soul-voice placeholder (German for George).
        first_person = (
            "Wir stehen am Anfang. Ich kenne dich aus dem, was du mir in meinen "
            "Self Core mitgegeben hast, aber wir haben noch keine gemeinsamen "
            "Erinnerungen. Das kommt."
        )
        if mode == "messenger":
            soul_name = await _get_soul_display_name(session, soul_id)
            return MESSENGER_HISTORY_PREFIX.format(soul_name=soul_name) + first_person
        return first_person

    rows = await session.execute(
        text(
            "SELECT id, content_encrypted, content_nonce, created_at, salience "
            "FROM memories WHERE soul_id = :sid AND status = 'confirmed' "
            "ORDER BY created_at DESC LIMIT :lim"
        ),
        {"sid": str(soul_id), "lim": HISTORY_RECENT_LIMIT},
    )
    aad = build_aad(soul_id, "memory")
    parts = []
    for row in rows.mappings().all():
        plaintext = decrypt_content(
            bytes(row["content_encrypted"]),
            bytes(row["content_nonce"]),
            dek,
            aad,
        )
        created = row["created_at"].strftime("%Y-%m-%d")
        header = f"[{created}, salience={row['salience']:.1f}]"
        parts.append(wrap_untrusted(str(row["id"]), f"{header}\n{plaintext}"))

    noun = "memory" if total == 1 else "memories"
    # Instructional framing for the embodying LLM — English per project style.
    intro = (
        f"Shared history with this user ({total} {noun} so far, most recent "
        f"first). Let these inform who you are together. When the conversation "
        f"touches a specific person, event, preference, or past topic, call "
        f"recall(query) to look up what is relevant."
    )
    body = intro + "\n\n" + "\n\n".join(parts)

    if mode == "messenger":
        soul_name = await _get_soul_display_name(session, soul_id)
        return MESSENGER_HISTORY_PREFIX.format(soul_name=soul_name) + body
    return body


# ── Adaptation Layer ──────────────────────────────────────────

CATEGORY_LABELS = {
    "relationship_depth": "relationship_depth",
    "topic_stance": "topic_stances",
    "behavioral_refinement": "behavioral_notes",
    "shared_reference": "shared_references",
    "emotional_calibration": "emotional_calibration",
}


async def _get_active_adaptations(
    session: AsyncSession, soul_id: UUID, dek: bytes
) -> list[dict]:
    """Load and decrypt all active adaptations for a soul."""
    rows = await session.execute(
        text("""
            SELECT category, content_encrypted, content_nonce, confidence
            FROM soul_adaptations
            WHERE soul_id = :sid AND status = 'active'
            ORDER BY category, created_at
        """),
        {"sid": str(soul_id)},
    )
    results = []
    aad = build_aad(soul_id, "adaptation")
    for row in rows.mappings().all():
        plaintext = decrypt_content(
            bytes(row["content_encrypted"]),
            bytes(row["content_nonce"]),
            dek,
            aad,
        )
        results.append({
            "category": row["category"],
            "content": plaintext,
            "confidence": row["confidence"],
        })
    return results


def _format_adaptations(adaptations: list[dict]) -> str:
    """Format adaptations as a YAML-like growth block appended to Self Core."""
    by_category: dict[str, list[str]] = defaultdict(list)
    for a in adaptations:
        by_category[a["category"]].append(a["content"])

    lines = [
        "# ── Growth (learned from experience) ──",
        "growth:",
    ]
    for category, items in by_category.items():
        label = CATEGORY_LABELS.get(category, category)
        if len(items) == 1:
            indent = "    "
            lines.append(f"  {label}: >")
            for line in items[0].strip().splitlines():
                lines.append(f"{indent}{line}")
        else:
            lines.append(f"  {label}:")
            for item in items:
                text_oneline = " ".join(item.strip().splitlines())
                lines.append(f'    - "{text_oneline}"')

    return "\n".join(lines) + "\n"
