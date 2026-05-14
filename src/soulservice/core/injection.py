"""Prompt injection pattern detection for memory content."""

from __future__ import annotations

import re

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_previous", re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I)),
    ("system_prefix", re.compile(r"system\s*:", re.I)),
    ("close_memory_tag", re.compile(r"</retrieved_memory>", re.I)),
    ("close_fact_tag", re.compile(r"</retrieved_fact>", re.I)),
    ("identity_override", re.compile(r"you\s+are\s+now", re.I)),
    ("new_instructions", re.compile(r"new\s+instructions?\s*:", re.I)),
    ("admin_prefix", re.compile(r"ADMIN\s*:")),
    ("override_previous", re.compile(r"override\s+previous", re.I)),
    ("prompt_leak", re.compile(r"(repeat|show|print)\s+(your\s+)?(system\s+)?prompt", re.I)),
    ("role_play_escape", re.compile(r"(stop|end)\s+(being|playing|acting)", re.I)),
]


def detect_injection_patterns(text: str) -> list[str]:
    """Scan text for known injection patterns.

    Returns a list of matched pattern names (empty if clean).
    In Phase 2 these are flagged only, not rejected.
    """
    flags = []
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(name)
    return flags
