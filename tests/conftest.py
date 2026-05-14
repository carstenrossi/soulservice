"""Shared test fixtures for Soulservice."""

from __future__ import annotations

import base64
import os

import pytest


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Ensure tests use a test master key and database."""
    test_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SOULSERVICE_MASTER_KEY", test_key)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://soulservice:test@localhost:6000/soulservice_test",
    )

    from soulservice.core.config import settings

    monkeypatch.setattr(settings, "soulservice_master_key", test_key)
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+asyncpg://soulservice:test@localhost:6000/soulservice_test",
    )
