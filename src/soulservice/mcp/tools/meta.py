"""Meta tools: health, whoami, stats."""

from __future__ import annotations


def health_check() -> dict:
    return {"status": "ok"}


def whoami_info(
    tenant_name: str, user_name: str, soul_slug: str, soul_display: str
) -> dict:
    return {
        "tenant": tenant_name,
        "user": user_name,
        "soul_slug": soul_slug,
        "soul_display_name": soul_display,
    }
