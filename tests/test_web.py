"""Tests for the web UI: auth helpers, RBAC, and route integration.

The DB layer is mocked throughout (per the plan: "DB-abhaengige Teile mocken"),
so these tests are deterministic and require no running Postgres. They exercise
the magic-link semantics, the POST-confirm flow, login throttling, graceful
decryption, optimistic locking, and role-based access control.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def web_settings(monkeypatch):
    from soulservice.core.config import settings

    monkeypatch.setattr(settings, "web_session_secret", "test-secret-key-for-sessions-32b!")
    monkeypatch.setattr(
        settings,
        "web_admin_emails",
        "admin@test.dev:admin,editor@test.dev:editor,viewer@test.dev:viewer",
    )
    monkeypatch.setattr(settings, "web_base_url", "http://testserver")
    monkeypatch.setattr(settings, "web_secure_cookies", False)


@pytest.fixture
async def client(web_settings):
    from soulservice.web.app import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


_SOUL = {
    "id": "00000000-0000-0000-0000-000000000001",
    "tenant_id": "00000000-0000-0000-0000-000000000002",
    "owner_user_id": "00000000-0000-0000-0000-000000000003",
    "slug": "george",
    "display_name": "George",
}
_SOULS = [{"slug": "george", "display_name": "George"}]


def _dummy_session_cm() -> MagicMock:
    """A MagicMock that behaves like a session factory (async context manager)."""
    factory = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm
    return factory


def _fake_soul_context(soul=None, souls=None):
    """Build a stand-in for web.db.soul_context yielding (soul, souls, session)."""
    soul = soul or _SOUL
    souls = souls if souls is not None else _SOULS

    @asynccontextmanager
    async def _cm(slug: str):
        yield soul, souls, AsyncMock()

    return _cm


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    """Minimal async session double for testing auth.consume logic."""

    def __init__(self, row):
        self._row = row
        self.executed: list = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _FakeResult(self._row)

    async def commit(self):
        return None


async def _login_as(client, email: str) -> None:
    """Drive the POST verify route (DB mocked) so the client holds a session."""
    with (
        patch(
            "soulservice.web.routes.auth_routes.app_session_factory",
            _dummy_session_cm(),
        ),
        patch(
            "soulservice.web.routes.auth_routes.consume_magic_link_token",
            new=AsyncMock(return_value=email),
        ),
    ):
        resp = await client.post(
            "/auth/verify", data={"token": "tok"}, follow_redirects=False
        )
        assert resp.status_code == 303


# -- Pure helpers ---------------------------------------------------


class TestAuthHelpers:
    def test_is_allowed_email(self, web_settings):
        from soulservice.web.auth import is_allowed_email

        assert is_allowed_email("admin@test.dev")
        assert is_allowed_email("  ADMIN@test.dev  ")
        assert not is_allowed_email("other@test.dev")

    def test_hash_token_deterministic(self):
        from soulservice.web.auth import _hash_token

        assert _hash_token("abc") == hashlib.sha256(b"abc").hexdigest()

    def test_role_parsing(self, web_settings):
        from soulservice.core.config import settings

        roles = settings.web_admin_roles
        assert roles["admin@test.dev"] == "admin"
        assert roles["editor@test.dev"] == "editor"
        assert roles["viewer@test.dev"] == "viewer"

    def test_bare_email_defaults_to_admin(self, monkeypatch):
        from soulservice.core.config import settings

        monkeypatch.setattr(settings, "web_admin_emails", "boss@x.dev")
        assert settings.web_admin_roles["boss@x.dev"] == "admin"

    def test_unknown_role_falls_back_to_viewer(self, monkeypatch):
        from soulservice.core.config import settings

        monkeypatch.setattr(settings, "web_admin_emails", "x@x.dev:superuser")
        assert settings.web_admin_roles["x@x.dev"] == "viewer"


class TestSafeDecrypt:
    def test_fallback_on_bad_input(self):
        from soulservice.web.queries import (
            DECRYPTION_FAILED_PLACEHOLDER,
            _safe_decrypt,
        )

        text, failed = _safe_decrypt(b"\x00", b"\x00", b"\x01" * 32, b"aad")
        assert failed
        assert text == DECRYPTION_FAILED_PLACEHOLDER

    def test_roundtrip(self):
        from soulservice.core.crypto import encrypt_content
        from soulservice.web.queries import _safe_decrypt

        dek = b"\x01" * 32
        aad = b"aad"
        ct, nonce = encrypt_content("hello", dek, aad)
        text, failed = _safe_decrypt(ct, nonce, dek, aad)
        assert not failed
        assert text == "hello"


# -- One-time / expiry semantics of magic-link tokens ---------------


class TestMagicLinkSemantics:
    pytestmark = pytest.mark.asyncio

    async def test_unused_token_returns_email(self):
        from soulservice.web.auth import consume_magic_link_token

        row = {
            "email": "admin@test.dev",
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            "used_at": None,
        }
        session = _FakeSession(row)
        assert await consume_magic_link_token(session, "tok") == "admin@test.dev"
        assert any("UPDATE web_login_tokens" in sql for sql, _ in session.executed)

    async def test_used_token_returns_none(self):
        from soulservice.web.auth import consume_magic_link_token

        row = {
            "email": "admin@test.dev",
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            "used_at": datetime.now(UTC),
        }
        assert await consume_magic_link_token(_FakeSession(row), "tok") is None

    async def test_expired_token_returns_none(self):
        from soulservice.web.auth import consume_magic_link_token

        row = {
            "email": "admin@test.dev",
            "expires_at": datetime.now(UTC) - timedelta(minutes=1),
            "used_at": None,
        }
        assert await consume_magic_link_token(_FakeSession(row), "tok") is None

    async def test_missing_token_returns_none(self):
        from soulservice.web.auth import consume_magic_link_token

        assert await consume_magic_link_token(_FakeSession(None), "tok") is None


# -- App startup / basic routes -------------------------------------


class TestAppStartup:
    def test_missing_secret_fails_closed(self, monkeypatch):
        from soulservice.core.config import settings
        from soulservice.web.app import create_app

        monkeypatch.setattr(settings, "web_session_secret", "")
        with pytest.raises(RuntimeError, match="WEB_SESSION_SECRET"):
            create_app()


class TestWebRoutesBasic:
    pytestmark = pytest.mark.asyncio

    async def test_health(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_login_page_renders(self, client):
        response = await client.get("/login")
        assert response.status_code == 200
        assert "Send magic link" in response.text

    async def test_dashboard_redirects_without_session(self, client):
        response = await client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    async def test_protected_route_without_session_redirects(self, client):
        response = await client.get(
            "/souls/george/proposals", follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


# -- Login + verify flow --------------------------------------------


class TestLoginFlow:
    pytestmark = pytest.mark.asyncio

    async def test_allowed_email_sends_link(self, client):
        with (
            patch(
                "soulservice.web.routes.auth_routes.app_session_factory",
                _dummy_session_cm(),
            ),
            patch(
                "soulservice.web.routes.auth_routes.create_magic_link_token",
                new=AsyncMock(return_value="rawtoken123"),
            ),
            patch(
                "soulservice.web.routes.auth_routes.send_magic_link",
                new=AsyncMock(),
            ) as mock_send,
        ):
            response = await client.post(
                "/login", data={"email": "admin@test.dev"}, follow_redirects=False
            )
        assert response.status_code == 200
        mock_send.assert_awaited_once()
        assert "rawtoken123" in mock_send.call_args[0][1]

    async def test_unauthorized_email_sends_nothing(self, client):
        with (
            patch(
                "soulservice.web.routes.auth_routes.create_magic_link_token",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "soulservice.web.routes.auth_routes.send_magic_link",
                new=AsyncMock(),
            ) as mock_send,
        ):
            response = await client.post(
                "/login", data={"email": "intruder@evil.dev"}, follow_redirects=False
            )
        assert response.status_code == 200
        mock_create.assert_not_called()
        mock_send.assert_not_called()

    async def test_login_throttle_caps_mail(self, client):
        from soulservice.web.throttle import LoginThrottle

        with (
            patch(
                "soulservice.web.routes.auth_routes.login_throttle",
                LoginThrottle(per_hour=2),
            ),
            patch(
                "soulservice.web.routes.auth_routes.app_session_factory",
                _dummy_session_cm(),
            ),
            patch(
                "soulservice.web.routes.auth_routes.create_magic_link_token",
                new=AsyncMock(return_value="t"),
            ),
            patch(
                "soulservice.web.routes.auth_routes.send_magic_link",
                new=AsyncMock(),
            ) as mock_send,
        ):
            for _ in range(5):
                await client.post("/login", data={"email": "admin@test.dev"})
        assert mock_send.await_count == 2

    async def test_verify_get_renders_confirm_without_consuming(self, client):
        with patch(
            "soulservice.web.routes.auth_routes.consume_magic_link_token",
            new=AsyncMock(),
        ) as mock_consume:
            response = await client.get("/auth/verify?token=abc")
        assert response.status_code == 200
        assert "Confirm login" in response.text
        mock_consume.assert_not_called()

    async def test_verify_post_sets_session_and_is_one_time(self, client):
        with (
            patch(
                "soulservice.web.routes.auth_routes.app_session_factory",
                _dummy_session_cm(),
            ),
            patch(
                "soulservice.web.routes.auth_routes.consume_magic_link_token",
                new=AsyncMock(return_value="admin@test.dev"),
            ),
        ):
            response = await client.post(
                "/auth/verify", data={"token": "tok"}, follow_redirects=False
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        with (
            patch(
                "soulservice.web.routes.auth_routes.app_session_factory",
                _dummy_session_cm(),
            ),
            patch(
                "soulservice.web.routes.auth_routes.consume_magic_link_token",
                new=AsyncMock(return_value=None),
            ),
        ):
            response = await client.post(
                "/auth/verify", data={"token": "tok"}, follow_redirects=False
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


# -- Authenticated views + RBAC -------------------------------------


class TestAuthenticatedViews:
    pytestmark = pytest.mark.asyncio

    async def test_dashboard_renders(self, client):
        await _login_as(client, "admin@test.dev")
        data = {
            "counts": {
                "memories_confirmed": 3,
                "memories_pending": 1,
                "facts": 2,
                "properties": 1,
                "tokens_active": 1,
                "self_core_version": 4,
            },
            "recent_audit": [],
        }
        with (
            patch(
                "soulservice.web.routes.dashboard.app_session_factory",
                _dummy_session_cm(),
            ),
            patch(
                "soulservice.web.queries.list_souls",
                new=AsyncMock(return_value=_SOULS),
            ),
            patch(
                "soulservice.web.routes.dashboard.soul_context",
                _fake_soul_context(),
            ),
            patch(
                "soulservice.web.queries.get_dashboard_data",
                new=AsyncMock(return_value=data),
            ),
        ):
            response = await client.get("/")
        assert response.status_code == 200
        assert "Dashboard" in response.text
        assert "George" in response.text


class TestRBAC:
    pytestmark = pytest.mark.asyncio

    async def test_viewer_cannot_decide_proposal(self, client):
        await _login_as(client, "viewer@test.dev")
        response = await client.post(
            "/souls/george/proposals/abc/decide",
            data={"action": "confirm"},
            follow_redirects=False,
        )
        assert response.status_code == 403

    async def test_editor_can_decide_proposal(self, client):
        await _login_as(client, "editor@test.dev")
        with (
            patch(
                "soulservice.web.routes.proposals.soul_context",
                _fake_soul_context(),
            ),
            patch(
                "soulservice.web.queries.decide_proposal_web",
                new=AsyncMock(),
            ) as mock_decide,
        ):
            response = await client.post(
                "/souls/george/proposals/abc/decide", data={"action": "confirm"}
            )
        assert response.status_code == 200
        mock_decide.assert_awaited_once()
        assert mock_decide.call_args[0][2] == "abc"
        assert mock_decide.call_args[0][3] == "confirm"

    async def test_editor_cannot_create_token(self, client):
        await _login_as(client, "editor@test.dev")
        response = await client.post(
            "/souls/george/tokens", data={"name": "x"}, follow_redirects=False
        )
        assert response.status_code == 403

    async def test_admin_can_create_token(self, client):
        await _login_as(client, "admin@test.dev")
        with (
            patch(
                "soulservice.web.routes.tokens.soul_context",
                _fake_soul_context(),
            ),
            patch(
                "soulservice.web.queries.create_token_web",
                new=AsyncMock(return_value=("sol_fulltoken", {})),
            ) as mock_create,
        ):
            response = await client.post(
                "/souls/george/tokens", data={"name": "x"}, follow_redirects=False
            )
        assert response.status_code == 303
        mock_create.assert_awaited_once()


class TestMemoryRevoke:
    pytestmark = pytest.mark.asyncio

    async def test_viewer_cannot_revoke(self, client):
        await _login_as(client, "viewer@test.dev")
        response = await client.post(
            "/souls/george/memories/abc/forget", follow_redirects=False
        )
        assert response.status_code == 403

    async def test_editor_can_revoke(self, client):
        await _login_as(client, "editor@test.dev")
        with (
            patch(
                "soulservice.web.routes.memories.soul_context",
                _fake_soul_context(),
            ),
            patch(
                "soulservice.web.queries.forget_memory_web",
                new=AsyncMock(),
            ) as mock_forget,
        ):
            response = await client.post(
                "/souls/george/memories/abc/forget", follow_redirects=False
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/souls/george/memories"
        mock_forget.assert_awaited_once()
        assert mock_forget.call_args[0][2] == "abc"


class TestSelfCoreConcurrency:
    pytestmark = pytest.mark.asyncio

    async def test_conflict_shows_reload_hint(self, client):
        await _login_as(client, "editor@test.dev")
        conflict = HTTPException(
            status_code=409, detail="Self Core was modified since you loaded it (now v7)."
        )
        with (
            patch(
                "soulservice.web.routes.self_core.soul_context",
                _fake_soul_context(),
            ),
            patch(
                "soulservice.web.queries.save_self_core",
                new=AsyncMock(side_effect=conflict),
            ),
            patch(
                "soulservice.web.queries.load_self_core",
                new=AsyncMock(return_value=("current: yaml\n", 7)),
            ),
        ):
            response = await client.post(
                "/souls/george/self-core",
                data={"content": "x: 1", "note": "", "version": "3"},
            )
        assert response.status_code == 200
        assert "modified" in response.text.lower()


def _minimal_export_zip() -> bytes:
    import io
    import json
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "source": {"slug": "george", "display_name": "George"},
                    "self_core": {"content": None, "history": []},
                    "facts": [],
                    "properties": [],
                    "adaptations": [],
                }
            ),
        )
        zf.writestr("memories.ndjson", "")
    return buf.getvalue()


class TestPortabilityRoutes:
    pytestmark = pytest.mark.asyncio

    async def test_viewer_cannot_export(self, client):
        await _login_as(client, "viewer@test.dev")
        response = await client.post(
            "/souls/george/export",
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 403

    async def test_editor_cannot_export(self, client):
        await _login_as(client, "editor@test.dev")
        response = await client.post(
            "/souls/george/export",
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 403

    async def test_admin_can_export(self, client):
        await _login_as(client, "admin@test.dev")
        fake_manifest = {"schema_version": 1, "source": {}, "self_core": {}}
        with (
            patch(
                "soulservice.web.routes.portability.soul_context",
                _fake_soul_context(),
            ),
            patch(
                "soulservice.web.routes.portability.portability.export_soul",
                new=AsyncMock(return_value=(fake_manifest, [])),
            ),
        ):
            response = await client.post(
                "/souls/george/export",
                data={},
                follow_redirects=False,
            )
        assert response.status_code == 200
        assert "application/zip" in response.headers.get("content-type", "")
        assert "george-export.zip" in response.headers.get("content-disposition", "")

    async def test_viewer_cannot_import(self, client):
        await _login_as(client, "viewer@test.dev")
        response = await client.post(
            "/souls/george/import",
            files={"file": ("export.zip", _minimal_export_zip(), "application/zip")},
            data={"mode": "merge"},
            follow_redirects=False,
        )
        assert response.status_code == 403

    async def test_editor_cannot_import(self, client):
        await _login_as(client, "editor@test.dev")
        response = await client.post(
            "/souls/george/import",
            files={"file": ("export.zip", _minimal_export_zip(), "application/zip")},
            data={"mode": "merge"},
            follow_redirects=False,
        )
        assert response.status_code == 403

    async def test_admin_import_merge_calls_import_soul(self, client):
        await _login_as(client, "admin@test.dev")
        stats = {
            "soul_id": "00000000-0000-0000-0000-000000000099",
            "created_new": False,
            "self_core": 1,
            "memories": 2,
            "facts": 0,
            "properties": 0,
            "adaptations": 0,
            "skipped_properties": 0,
        }

        @asynccontextmanager
        async def _fake_get_session():
            yield AsyncMock()

        with (
            patch(
                "soulservice.web.routes.portability.get_session",
                _fake_get_session,
            ),
            patch(
                "soulservice.web.routes.portability.portability.import_soul",
                new=AsyncMock(return_value=stats),
            ) as mock_import,
        ):
            response = await client.post(
                "/souls/george/import",
                files={"file": ("export.zip", _minimal_export_zip(), "application/zip")},
                data={"mode": "merge", "on_conflict": "overwrite"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/souls/george/portability"
        mock_import.assert_awaited_once()
        assert mock_import.call_args.kwargs["into_slug"] == "george"
        assert mock_import.call_args.kwargs["owner_user_id"] is None

    async def test_admin_import_new_calls_import_soul(self, client):
        await _login_as(client, "admin@test.dev")
        owner_id = "00000000-0000-0000-0000-000000000003"
        stats = {
            "soul_id": "00000000-0000-0000-0000-000000000099",
            "created_new": True,
            "self_core": 1,
            "memories": 0,
            "facts": 0,
            "properties": 0,
            "adaptations": 0,
            "skipped_properties": 0,
        }

        @asynccontextmanager
        async def _fake_get_session():
            yield AsyncMock()

        with (
            patch(
                "soulservice.web.routes.portability.get_session",
                _fake_get_session,
            ),
            patch(
                "soulservice.web.routes.portability.portability.import_soul",
                new=AsyncMock(return_value=stats),
            ) as mock_import,
        ):
            response = await client.post(
                "/souls/george/import",
                files={"file": ("export.zip", _minimal_export_zip(), "application/zip")},
                data={
                    "mode": "new",
                    "owner_user_id": owner_id,
                    "new_slug": "george-copy",
                    "on_conflict": "skip",
                },
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/souls/george-copy/portability"
        mock_import.assert_awaited_once()
        assert mock_import.call_args.kwargs["into_slug"] is None
        assert mock_import.call_args.kwargs["owner_user_id"] == owner_id
        assert mock_import.call_args.kwargs["new_slug"] == "george-copy"
