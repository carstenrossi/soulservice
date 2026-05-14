"""Tests for messenger-mode response framing in identity tools."""

from __future__ import annotations

from soulservice.mcp.tools.identity import (
    MESSENGER_HISTORY_PREFIX,
    MESSENGER_SELF_CORE_PREFIX,
)


def test_messenger_self_core_prefix_contains_soul_name():
    result = MESSENGER_SELF_CORE_PREFIX.format(soul_name="George")
    assert "George" in result
    assert "Speak AS the Soul" in result


def test_messenger_self_core_prefix_ends_with_separator():
    result = MESSENGER_SELF_CORE_PREFIX.format(soul_name="George")
    assert result.endswith("---\n\n")


def test_messenger_history_prefix_contains_soul_name():
    result = MESSENGER_HISTORY_PREFIX.format(soul_name="George")
    assert "George" in result
    assert "Relationship context" in result


def test_identity_mode_constants_are_not_empty():
    assert len(MESSENGER_SELF_CORE_PREFIX) > 50
    assert len(MESSENGER_HISTORY_PREFIX) > 10
