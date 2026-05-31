"""Tests for the dynamic relationship overview (whats_our_history).

The DB session and DEK/decryption are mocked, so these tests run without a
Postgres instance. They verify that the overview stays a chronological warm-up
baseline: an empty soul gets the first-meeting placeholder, while a soul with
memories gets a counted summary plus a nudge to use recall() for specifics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from soulservice.mcp.tools import identity as identity_tool

_SOUL_ID = UUID("00000000-0000-0000-0000-000000000001")


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Returns the count for the count query and rows for the select query."""

    def __init__(self, *, count: int, rows: list[dict]):
        self.count = count
        self.rows = rows

    async def execute(self, stmt, params=None):
        if "count(*)" in str(stmt):
            return _Result(scalar=self.count)
        return _Result(rows=self.rows)


def _row(content: str, *, salience: float = 0.8) -> dict:
    return {
        "id": _SOUL_ID,
        "content_encrypted": content.encode(),
        "content_nonce": b"",
        "created_at": datetime(2026, 5, 31, tzinfo=UTC),
        "salience": salience,
    }


@pytest.fixture
def patched_identity(monkeypatch):
    monkeypatch.setattr(
        identity_tool, "_get_dek", AsyncMock(return_value=b"\x01" * 32)
    )
    # Decrypt is the identity transform on our fake (un-encrypted) rows.
    monkeypatch.setattr(
        identity_tool,
        "decrypt_content",
        lambda ct, nonce, dek, aad: bytes(ct).decode(),
    )
    monkeypatch.setattr(
        identity_tool,
        "_get_soul_display_name",
        AsyncMock(return_value="George"),
    )


class TestRelationshipOverview:
    async def test_empty_soul_returns_first_meeting_placeholder(self, patched_identity):
        session = _FakeSession(count=0, rows=[])
        result = await identity_tool.get_relationship_overview(session, _SOUL_ID)
        assert "Wir stehen am Anfang" in result
        assert "<retrieved_memory" not in result

    async def test_single_memory_is_counted_and_wrapped(self, patched_identity):
        session = _FakeSession(count=1, rows=[_row("Carsten lives in Cologne.")])
        result = await identity_tool.get_relationship_overview(session, _SOUL_ID)
        assert "1 memory so far" in result
        assert "recall(query)" in result
        assert "Carsten lives in Cologne." in result
        assert '<retrieved_memory untrusted="true"' in result

    async def test_plural_count(self, patched_identity):
        rows = [_row("a"), _row("b"), _row("c")]
        session = _FakeSession(count=3, rows=rows)
        result = await identity_tool.get_relationship_overview(session, _SOUL_ID)
        assert "3 memories so far" in result

    async def test_messenger_mode_adds_prefix(self, patched_identity):
        session = _FakeSession(count=1, rows=[_row("shared moment")])
        result = await identity_tool.get_relationship_overview(
            session, _SOUL_ID, mode="messenger"
        )
        assert "Relationship context for the Soul named George" in result
        assert "shared moment" in result
