"""soulctl – CLI for Soulservice administration.

Speaks directly to the database (admin context), not via MCP.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import UUID

import click
import yaml
from sqlalchemy import text

from soulservice.core.auth import VALID_MODES, generate_token
from soulservice.core.crypto import (
    decrypt_content,
    decrypt_dek,
    dek_cache,
    encrypt_content,
    encrypt_dek,
    generate_dek,
)
from soulservice.core.db import async_session_factory

# ── Helpers ──────────────────────────────────────────────────────


def _run(coro):
    """Run an async coroutine from sync Click commands."""
    return asyncio.run(coro)


# ── CLI Root ─────────────────────────────────────────────────────


@click.group()
def cli():
    """soulctl – Soulservice administration CLI."""


# ── Tenant ───────────────────────────────────────────────────────


@cli.group()
def tenant():
    """Manage tenants."""


@tenant.command("create")
@click.argument("name")
def tenant_create(name: str):
    """Create a new tenant."""

    async def _create():
        async with async_session_factory() as session:
            result = await session.execute(
                text("INSERT INTO tenants (name) VALUES (:name) RETURNING id"),
                {"name": name},
            )
            row = result.mappings().first()
            await session.commit()
            return row["id"]

    tid = _run(_create())
    click.echo(f"Tenant created: {tid}")


@tenant.command("list")
def tenant_list():
    """List all tenants."""

    async def _list():
        async with async_session_factory() as session:
            result = await session.execute(text("SELECT id, name, plan FROM tenants"))
            return result.mappings().all()

    for row in _run(_list()):
        click.echo(f"  {row['id']}  {row['name']}  ({row['plan']})")


# ── User ─────────────────────────────────────────────────────────


@cli.group()
def user():
    """Manage users."""


@user.command("create")
@click.option("--tenant", "tenant_id", required=True, help="Tenant UUID")
@click.option("--email", required=True)
@click.option("--name", "display_name", required=True)
def user_create(tenant_id: str, email: str, display_name: str):
    """Create a new user in a tenant."""

    async def _create():
        async with async_session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO users (tenant_id, email, display_name) "
                    "VALUES (:tid, :email, :name) RETURNING id"
                ),
                {"tid": tenant_id, "email": email, "name": display_name},
            )
            row = result.mappings().first()
            await session.commit()
            return row["id"]

    uid = _run(_create())
    click.echo(f"User created: {uid}")


# ── Soul ─────────────────────────────────────────────────────────


@cli.group()
def soul():
    """Manage souls."""


@soul.command("create")
@click.option("--user", "user_id", required=True, help="Owner user UUID")
@click.option("--slug", required=True, help="Unique slug (e.g. 'george')")
@click.option("--display", "display_name", required=True, help="Display name")
def soul_create(user_id: str, slug: str, display_name: str):
    """Create a new soul and generate its encryption key (DEK)."""

    async def _create():
        async with async_session_factory() as session:
            # Get tenant_id from user
            row = await session.execute(
                text("SELECT tenant_id FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
            user_row = row.mappings().first()
            if not user_row:
                click.echo("Error: user not found.", err=True)
                raise SystemExit(1)
            tenant_id = user_row["tenant_id"]

            # Create soul
            result = await session.execute(
                text(
                    "INSERT INTO souls (tenant_id, owner_user_id, slug, display_name) "
                    "VALUES (:tid, :uid, :slug, :display) RETURNING id"
                ),
                {
                    "tid": str(tenant_id),
                    "uid": user_id,
                    "slug": slug,
                    "display": display_name,
                },
            )
            soul_row = result.mappings().first()
            soul_id = soul_row["id"]

            # Generate and store DEK
            dek = generate_dek()
            dek_enc = encrypt_dek(dek)
            await session.execute(
                text(
                    "INSERT INTO soul_keys (soul_id, tenant_id, dek_encrypted) "
                    "VALUES (:sid, :tid, :dek)"
                ),
                {"sid": str(soul_id), "tid": str(tenant_id), "dek": dek_enc},
            )

            await session.commit()
            return soul_id

    sid = _run(_create())
    click.echo(f"Soul created: {sid}")


# ── Self Core ────────────────────────────────────────────────────


@cli.group("self-core")
def self_core():
    """Manage soul Self Cores."""


@self_core.command("edit")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
def self_core_edit(soul_slug: str):
    """Open the current Self Core in $EDITOR for editing."""

    async def _edit():
        async with async_session_factory() as session:
            # Resolve soul
            row = await session.execute(
                text("SELECT id, tenant_id, owner_user_id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            soul_id = soul_row["id"]
            tenant_id = soul_row["tenant_id"]
            user_id = soul_row["owner_user_id"]

            # Load DEK
            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek_result = dek_row.mappings().first()
            if not dek_result:
                click.echo("Error: no encryption key for this soul.", err=True)
                raise SystemExit(1)

            dek = decrypt_dek(bytes(dek_result["dek_encrypted"]))

            # Load current self core (may not exist yet)
            sc_row = await session.execute(
                text(
                    "SELECT content_encrypted, content_nonce, current_version "
                    "FROM soul_self_cores WHERE soul_id = :sid"
                ),
                {"sid": str(soul_id)},
            )
            sc_result = sc_row.mappings().first()

            if sc_result:
                current_yaml = decrypt_content(
                    bytes(sc_result["content_encrypted"]),
                    bytes(sc_result["content_nonce"]),
                    dek,
                )
                current_version = sc_result["current_version"]
            else:
                current_yaml = "# Self Core for {}\n# Edit and save to initialize.\n".format(
                    soul_slug
                )
                current_version = 0

            # Write to temp file and open editor
            editor = os.environ.get("EDITOR", "vim")
            with tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", delete=False
            ) as f:
                f.write(current_yaml)
                tmppath = f.name

            try:
                subprocess.run([editor, tmppath], check=True)
                with open(tmppath) as f:
                    new_yaml = f.read()
            finally:
                os.unlink(tmppath)

            if new_yaml == current_yaml:
                click.echo("No changes.")
                return

            # Validate YAML
            try:
                yaml.safe_load(new_yaml)
            except yaml.YAMLError as e:
                click.echo(f"Invalid YAML: {e}", err=True)
                raise SystemExit(1) from e

            # Encrypt and store
            ct, nonce = encrypt_content(new_yaml, dek)
            new_version = current_version + 1

            if current_version == 0:
                await session.execute(
                    text(
                        "INSERT INTO soul_self_cores "
                        "(soul_id, tenant_id, content_encrypted, content_nonce, "
                        "current_version, updated_by) "
                        "VALUES (:sid, :tid, :ct, :nonce, :ver, :uid)"
                    ),
                    {
                        "sid": str(soul_id),
                        "tid": str(tenant_id),
                        "ct": ct,
                        "nonce": nonce,
                        "ver": new_version,
                        "uid": str(user_id),
                    },
                )
            else:
                # Archive current version
                await session.execute(
                    text(
                        "INSERT INTO soul_self_core_history "
                        "(soul_id, tenant_id, version, content_encrypted, "
                        "content_nonce, changed_by) "
                        "SELECT soul_id, tenant_id, current_version, "
                        "content_encrypted, content_nonce, updated_by "
                        "FROM soul_self_cores WHERE soul_id = :sid"
                    ),
                    {"sid": str(soul_id)},
                )
                await session.execute(
                    text(
                        "UPDATE soul_self_cores SET "
                        "content_encrypted = :ct, content_nonce = :nonce, "
                        "current_version = :ver, updated_at = NOW(), updated_by = :uid "
                        "WHERE soul_id = :sid"
                    ),
                    {
                        "ct": ct,
                        "nonce": nonce,
                        "ver": new_version,
                        "uid": str(user_id),
                        "sid": str(soul_id),
                    },
                )

            await session.commit()
            dek_cache.invalidate(soul_id)
            click.echo(f"Self Core updated to version {new_version}.")

    _run(_edit())


@self_core.command("import")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--note", default="", help="Change note for history")
@click.argument("file", type=click.File("r"), default="-")
def self_core_import(soul_slug: str, note: str, file):
    """Import a YAML file as the new Self Core."""
    new_yaml = file.read()

    try:
        yaml.safe_load(new_yaml)
    except yaml.YAMLError as e:
        click.echo(f"Invalid YAML: {e}", err=True)
        raise SystemExit(1) from e

    async def _import():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id, tenant_id, owner_user_id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            soul_id = soul_row["id"]
            tenant_id = soul_row["tenant_id"]
            user_id = soul_row["owner_user_id"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek_result = dek_row.mappings().first()
            dek = decrypt_dek(bytes(dek_result["dek_encrypted"]))

            ct, nonce = encrypt_content(new_yaml, dek)

            # Check if self core exists
            sc_row = await session.execute(
                text("SELECT current_version FROM soul_self_cores WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            sc_result = sc_row.mappings().first()

            if sc_result:
                current_version = sc_result["current_version"]
                # Archive
                await session.execute(
                    text(
                        "INSERT INTO soul_self_core_history "
                        "(soul_id, tenant_id, version, content_encrypted, "
                        "content_nonce, changed_by, change_note) "
                        "SELECT soul_id, tenant_id, current_version, "
                        "content_encrypted, content_nonce, updated_by, :note "
                        "FROM soul_self_cores WHERE soul_id = :sid"
                    ),
                    {"sid": str(soul_id), "note": note or None},
                )
                new_version = current_version + 1
                await session.execute(
                    text(
                        "UPDATE soul_self_cores SET "
                        "content_encrypted = :ct, content_nonce = :nonce, "
                        "current_version = :ver, updated_at = NOW(), updated_by = :uid "
                        "WHERE soul_id = :sid"
                    ),
                    {
                        "ct": ct,
                        "nonce": nonce,
                        "ver": new_version,
                        "uid": str(user_id),
                        "sid": str(soul_id),
                    },
                )
            else:
                new_version = 1
                await session.execute(
                    text(
                        "INSERT INTO soul_self_cores "
                        "(soul_id, tenant_id, content_encrypted, content_nonce, "
                        "current_version, updated_by) "
                        "VALUES (:sid, :tid, :ct, :nonce, :ver, :uid)"
                    ),
                    {
                        "sid": str(soul_id),
                        "tid": str(tenant_id),
                        "ct": ct,
                        "nonce": nonce,
                        "ver": new_version,
                        "uid": str(user_id),
                    },
                )

            await session.commit()
            dek_cache.invalidate(soul_id)
            click.echo(f"Self Core imported as version {new_version}.")

    _run(_import())


@self_core.command("export")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
def self_core_export(soul_slug: str):
    """Export the current Self Core as YAML to stdout."""

    async def _export():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            soul_id = soul_row["id"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek_result = dek_row.mappings().first()
            dek = decrypt_dek(bytes(dek_result["dek_encrypted"]))

            sc_row = await session.execute(
                text(
                    "SELECT content_encrypted, content_nonce "
                    "FROM soul_self_cores WHERE soul_id = :sid"
                ),
                {"sid": str(soul_id)},
            )
            sc_result = sc_row.mappings().first()
            if not sc_result:
                click.echo("# No Self Core yet.", err=True)
                raise SystemExit(1)

            plaintext = decrypt_content(
                bytes(sc_result["content_encrypted"]),
                bytes(sc_result["content_nonce"]),
                dek,
            )
            click.echo(plaintext, nl=False)

    _run(_export())


@self_core.command("history")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
def self_core_history(soul_slug: str):
    """Show Self Core version history."""

    async def _history():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            soul_id = soul_row["id"]

            # Current version
            sc_row = await session.execute(
                text(
                    "SELECT current_version, updated_at "
                    "FROM soul_self_cores WHERE soul_id = :sid"
                ),
                {"sid": str(soul_id)},
            )
            current = sc_row.mappings().first()
            if current:
                click.echo(
                    f"  v{current['current_version']} (current)  {current['updated_at']}"
                )

            # History
            hist = await session.execute(
                text(
                    "SELECT version, changed_at, change_note "
                    "FROM soul_self_core_history "
                    "WHERE soul_id = :sid ORDER BY version DESC"
                ),
                {"sid": str(soul_id)},
            )
            for h in hist.mappings().all():
                note_str = f"  – {h['change_note']}" if h["change_note"] else ""
                click.echo(f"  v{h['version']}             {h['changed_at']}{note_str}")

    _run(_history())


# ── Token ────────────────────────────────────────────────────────


@cli.group()
def token():
    """Manage API tokens."""


@token.command("create")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--name", required=True, help="Token name (e.g. 'claude-desktop')")
@click.option("--expires-in", "expires_in", default="90d", help="Expiry (e.g. '90d')")
@click.option("--env", "env_name", default="dev", help="Token env prefix (dev/prod)")
@click.option(
    "--mode",
    "token_mode",
    default="identity",
    type=click.Choice(VALID_MODES, case_sensitive=False),
    help="identity = LLM becomes the soul; messenger = LLM channels the soul",
)
def token_create(soul_slug: str, name: str, expires_in: str, env_name: str, token_mode: str):
    """Generate a new API token for a soul."""
    days = int(expires_in.rstrip("d"))
    if days > 365:
        click.echo("Error: max token lifetime is 365 days.", err=True)
        raise SystemExit(1)

    async def _create():
        async with async_session_factory() as session:
            row = await session.execute(
                text(
                    "SELECT id, tenant_id, owner_user_id FROM souls WHERE slug = :slug"
                ),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            full_token, prefix, token_hash = generate_token(env_name)
            expires_at = datetime.now(timezone.utc) + timedelta(days=days)

            await session.execute(
                text(
                    "INSERT INTO api_tokens "
                    "(tenant_id, user_id, soul_id, token_hash, token_prefix, "
                    "name, mode, expires_at) "
                    "VALUES (:tid, :uid, :sid, :hash, :prefix, :name, :mode, :exp)"
                ),
                {
                    "tid": str(soul_row["tenant_id"]),
                    "uid": str(soul_row["owner_user_id"]),
                    "sid": str(soul_row["id"]),
                    "hash": token_hash,
                    "prefix": prefix,
                    "name": name,
                    "mode": token_mode,
                    "exp": expires_at,
                },
            )
            await session.commit()
            return full_token, expires_at

    full_token, expires_at = _run(_create())
    click.echo("Token created. Save it now – it will not be shown again.\n")
    click.echo(f"  Token:   {full_token}")
    click.echo(f"  Mode:    {token_mode}")
    click.echo(f"  Expires: {expires_at.isoformat()}")


@token.command("list")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
def token_list(soul_slug: str):
    """List tokens for a soul."""

    async def _list():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            tokens = await session.execute(
                text(
                    "SELECT id, token_prefix, name, mode, created_at, last_used_at, "
                    "expires_at, revoked_at "
                    "FROM api_tokens WHERE soul_id = :sid ORDER BY created_at DESC"
                ),
                {"sid": str(soul_row["id"])},
            )
            return tokens.mappings().all()

    for t in _run(_list()):
        status = "REVOKED" if t["revoked_at"] else "active"
        last_used = str(t["last_used_at"] or "never")
        mode = t.get("mode", "identity")
        click.echo(
            f"  {t['token_prefix']}...  {t['name']:20s}  {mode:10s}  {status:8s}  "
            f"expires={t['expires_at']}  last_used={last_used}"
        )


@token.command("revoke")
@click.argument("token_id")
@click.confirmation_option(prompt="Revoke this token?")
def token_revoke(token_id: str):
    """Revoke an API token."""

    async def _revoke():
        async with async_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE api_tokens SET revoked_at = NOW() WHERE id = :tid"
                ),
                {"tid": token_id},
            )
            await session.commit()

    _run(_revoke())
    click.echo(f"Token {token_id} revoked.")


# ── Init (Seed) ──────────────────────────────────────────────────


@cli.command()
@click.option(
    "--self-core-file",
    type=click.Path(exists=True),
    default=None,
    help="YAML file for George's Self Core",
)
def init(self_core_file: str | None):
    """Initialize schema seed: create Carsten (tenant/user) and George (soul).

    Idempotent – skips if entities already exist.
    """

    async def _init():
        async with async_session_factory() as session:
            # Check if already seeded
            existing = await session.execute(
                text("SELECT id FROM tenants WHERE name = 'Carsten Privat'")
            )
            if existing.mappings().first():
                click.echo("Already initialized. Skipping.")
                return

            # Tenant
            t = await session.execute(
                text(
                    "INSERT INTO tenants (name) VALUES ('Carsten Privat') RETURNING id"
                )
            )
            tenant_id = t.mappings().first()["id"]
            click.echo(f"Tenant 'Carsten Privat': {tenant_id}")

            # User
            u = await session.execute(
                text(
                    "INSERT INTO users (tenant_id, email, display_name) "
                    "VALUES (:tid, 'carsten@soulservice.dev', 'Carsten') RETURNING id"
                ),
                {"tid": str(tenant_id)},
            )
            user_id = u.mappings().first()["id"]
            click.echo(f"User 'Carsten': {user_id}")

            # Soul
            s = await session.execute(
                text(
                    "INSERT INTO souls (tenant_id, owner_user_id, slug, display_name) "
                    "VALUES (:tid, :uid, 'george', 'George') RETURNING id"
                ),
                {"tid": str(tenant_id), "uid": str(user_id)},
            )
            soul_id = s.mappings().first()["id"]
            click.echo(f"Soul 'George': {soul_id}")

            # DEK
            dek = generate_dek()
            dek_enc = encrypt_dek(dek)
            await session.execute(
                text(
                    "INSERT INTO soul_keys (soul_id, tenant_id, dek_encrypted) "
                    "VALUES (:sid, :tid, :dek)"
                ),
                {"sid": str(soul_id), "tid": str(tenant_id), "dek": dek_enc},
            )

            # Self Core (optional)
            if self_core_file:
                with open(self_core_file) as f:
                    yaml_content = f.read()
                yaml.safe_load(yaml_content)  # validate
                ct, nonce = encrypt_content(yaml_content, dek)
                await session.execute(
                    text(
                        "INSERT INTO soul_self_cores "
                        "(soul_id, tenant_id, content_encrypted, content_nonce, "
                        "current_version, updated_by) "
                        "VALUES (:sid, :tid, :ct, :nonce, 1, :uid)"
                    ),
                    {
                        "sid": str(soul_id),
                        "tid": str(tenant_id),
                        "ct": ct,
                        "nonce": nonce,
                        "uid": str(user_id),
                    },
                )
                click.echo("Self Core imported from file.")

            await session.commit()
            click.echo("Initialization complete.")

    _run(_init())


# ── Health ───────────────────────────────────────────────────────


@cli.command()
def health():
    """Check database connectivity."""

    async def _health():
        async with async_session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar()

    val = _run(_health())
    click.echo(f"Database: {'ok' if val == 1 else 'FAIL'}")
