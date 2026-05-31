"""Tests for soul export/import portability helpers."""

from __future__ import annotations

import pytest

from soulservice.core.portability import (
    EXPORT_SCHEMA_VERSION,
    build_manifest,
    format_embedding,
    memory_to_ndjson_line,
    parse_ndjson_line,
    validate_conflict_mode,
    validate_manifest,
)


class TestSchemaVersion:
    def test_schema_version_is_int(self):
        assert EXPORT_SCHEMA_VERSION == 1


class TestValidateManifest:
    def test_accepts_valid_manifest(self):
        validate_manifest({"schema_version": 1, "source": {}, "self_core": {}})

    def test_rejects_wrong_version(self):
        with pytest.raises(ValueError, match="schema_version"):
            validate_manifest({"schema_version": 2, "source": {}, "self_core": {}})

    def test_rejects_missing_version(self):
        with pytest.raises(ValueError, match="schema_version"):
            validate_manifest({"source": {}, "self_core": {}})

    def test_rejects_zero_version(self):
        with pytest.raises(ValueError, match="schema_version"):
            validate_manifest({"schema_version": 0, "source": {}, "self_core": {}})

    def test_rejects_missing_source(self):
        with pytest.raises(ValueError, match="source"):
            validate_manifest({"schema_version": 1, "self_core": {}})

    def test_rejects_missing_self_core(self):
        with pytest.raises(ValueError, match="self_core"):
            validate_manifest({"schema_version": 1, "source": {}})


class TestValidateConflictMode:
    def test_overwrite_ok(self):
        assert validate_conflict_mode("overwrite") == "overwrite"

    def test_skip_ok(self):
        assert validate_conflict_mode("skip") == "skip"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="on_conflict"):
            validate_conflict_mode("merge")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="on_conflict"):
            validate_conflict_mode("")


class TestFormatEmbedding:
    def test_formats_vector(self):
        assert format_embedding([0.1, 0.2]) == "[0.1,0.2]"


class TestNdjsonRoundtrip:
    def test_roundtrip_with_special_chars(self):
        rec = {
            "content": 'Line 1\nLine 2 with "quotes" and ünicode',
            "salience": 0.7,
            "status": "confirmed",
            "tags": ["work", "personal"],
            "injection_flags": [],
            "source_client": "cli",
            "created_at": "2026-05-31T18:00:00+00:00",
            "last_recalled_at": None,
            "recall_count": 0,
            "embedding": [0.01] * 4,
        }
        line = memory_to_ndjson_line(rec)
        parsed = parse_ndjson_line(line)
        assert parsed["content"] == rec["content"]
        assert parsed["tags"] == rec["tags"]
        assert parsed["embedding"] == rec["embedding"]


class TestBuildManifest:
    def test_sets_schema_and_timestamp(self):
        manifest = build_manifest({}, {}, [], [], [])
        assert manifest["schema_version"] == EXPORT_SCHEMA_VERSION
        assert "exported_at" in manifest
        assert manifest["audit"] == []

    def test_includes_audit_when_provided(self):
        audit = [{"tool_name": "recall", "status": "ok"}]
        manifest = build_manifest({}, {}, [], [], [], audit=audit)
        assert manifest["audit"] == audit

    def test_passes_through_lists(self):
        facts = [{"category": "x", "key": "y", "value": "z"}]
        manifest = build_manifest({"slug": "g"}, {"content": None}, facts, [], [])
        assert manifest["source"]["slug"] == "g"
        assert manifest["facts"] == facts
