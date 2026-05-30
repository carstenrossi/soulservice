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
from soulservice.core.embeddings import embed_text
from soulservice.models.adaptation import ADAPTATION_CATEGORIES

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


# ── Adaptation ────────────────────────────────────────────────


@cli.group()
def adaptation():
    """Manage soul adaptations (learned dispositions)."""


@adaptation.command("add")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option(
    "--category",
    required=True,
    type=click.Choice(ADAPTATION_CATEGORIES, case_sensitive=False),
    help="Adaptation category",
)
@click.option("--confidence", default=0.5, help="Confidence score 0.0-1.0")
@click.option("--source", default="manual", help="Source of this adaptation")
@click.argument("content")
def adaptation_add(soul_slug: str, category: str, confidence: float, source: str, content: str):
    """Add a new adaptation for a soul.

    CONTENT is the adaptation text in the soul's voice (first person).
    """

    async def _add():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id, tenant_id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            soul_id = soul_row["id"]
            tenant_id = soul_row["tenant_id"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek_result = dek_row.mappings().first()
            if not dek_result:
                click.echo("Error: no encryption key for this soul.", err=True)
                raise SystemExit(1)

            dek = decrypt_dek(bytes(dek_result["dek_encrypted"]))
            ct, nonce = encrypt_content(content, dek)

            result = await session.execute(
                text(
                    "INSERT INTO soul_adaptations "
                    "(tenant_id, soul_id, category, content_encrypted, content_nonce, "
                    "confidence, source) "
                    "VALUES (:tid, :sid, :cat, :ct, :nonce, :conf, :src) RETURNING id"
                ),
                {
                    "tid": str(tenant_id),
                    "sid": str(soul_id),
                    "cat": category,
                    "ct": ct,
                    "nonce": nonce,
                    "conf": confidence,
                    "src": source,
                },
            )
            adaptation_id = result.mappings().first()["id"]
            await session.commit()
            return adaptation_id

    aid = _run(_add())
    click.echo(f"Adaptation added: {aid}")


@adaptation.command("list")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--status", "filter_status", default="active", help="Filter by status")
def adaptation_list(soul_slug: str, filter_status: str):
    """List adaptations for a soul."""

    async def _list():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id, tenant_id FROM souls WHERE slug = :slug"),
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

            adaptations = await session.execute(
                text(
                    "SELECT id, category, content_encrypted, content_nonce, "
                    "confidence, source, created_at, status "
                    "FROM soul_adaptations WHERE soul_id = :sid AND status = :st "
                    "ORDER BY category, created_at"
                ),
                {"sid": str(soul_id), "st": filter_status},
            )
            results = []
            for a in adaptations.mappings().all():
                plaintext = decrypt_content(
                    bytes(a["content_encrypted"]),
                    bytes(a["content_nonce"]),
                    dek,
                )
                results.append({**a, "content": plaintext})
            return results

    for a in _run(_list()):
        preview = a["content"][:80].replace("\n", " ")
        if len(a["content"]) > 80:
            preview += "..."
        click.echo(
            f"  {str(a['id'])[:8]}...  {a['category']:25s}  "
            f"conf={a['confidence']:.1f}  {a['source'] or '-':10s}  {preview}"
        )


@adaptation.command("supersede")
@click.argument("adaptation_id")
@click.argument("new_content")
@click.option("--confidence", default=None, type=float, help="New confidence score")
def adaptation_supersede(adaptation_id: str, new_content: str, confidence: float | None):
    """Replace an adaptation with a new version, preserving history."""

    async def _supersede():
        async with async_session_factory() as session:
            old = await session.execute(
                text(
                    "SELECT soul_id, tenant_id, category, confidence "
                    "FROM soul_adaptations WHERE id = :aid AND status = 'active'"
                ),
                {"aid": adaptation_id},
            )
            old_row = old.mappings().first()
            if not old_row:
                click.echo("Error: adaptation not found or not active.", err=True)
                raise SystemExit(1)

            soul_id = old_row["soul_id"]
            tenant_id = old_row["tenant_id"]
            conf = confidence if confidence is not None else old_row["confidence"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek = decrypt_dek(bytes(dek_row.mappings().first()["dek_encrypted"]))
            ct, nonce = encrypt_content(new_content, dek)

            new = await session.execute(
                text(
                    "INSERT INTO soul_adaptations "
                    "(tenant_id, soul_id, category, content_encrypted, content_nonce, "
                    "confidence, source) "
                    "VALUES (:tid, :sid, :cat, :ct, :nonce, :conf, 'supersede') RETURNING id"
                ),
                {
                    "tid": str(tenant_id),
                    "sid": str(soul_id),
                    "cat": old_row["category"],
                    "ct": ct,
                    "nonce": nonce,
                    "conf": conf,
                },
            )
            new_id = new.mappings().first()["id"]

            await session.execute(
                text(
                    "UPDATE soul_adaptations SET status = 'superseded', "
                    "superseded_by = :new_id WHERE id = :old_id"
                ),
                {"new_id": str(new_id), "old_id": adaptation_id},
            )
            await session.commit()
            return new_id

    new_id = _run(_supersede())
    click.echo(f"Superseded. New adaptation: {new_id}")


# ── Proposals ─────────────────────────────────────────────────


@cli.group()
def proposals():
    """Review memory proposals."""


@proposals.command("list")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--status", "filter_status", default="pending", help="Filter by status")
def proposals_list(soul_slug: str, filter_status: str):
    """List memory proposals for a soul."""

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

            soul_id = soul_row["id"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek_result = dek_row.mappings().first()
            dek = decrypt_dek(bytes(dek_result["dek_encrypted"]))

            rows = await session.execute(
                text(
                    "SELECT id, content_encrypted, content_nonce, created_at, "
                    "salience, tags, injection_flags, source_client "
                    "FROM memories WHERE soul_id = :sid AND status = :st "
                    "ORDER BY created_at DESC LIMIT 50"
                ),
                {"sid": str(soul_id), "st": filter_status},
            )
            results = []
            for m in rows.mappings().all():
                plaintext = decrypt_content(
                    bytes(m["content_encrypted"]),
                    bytes(m["content_nonce"]),
                    dek,
                )
                results.append({**m, "content": plaintext})
            return results

    memories = _run(_list())
    if not memories:
        click.echo(f"No {filter_status} proposals.")
        return

    click.echo(f"{len(memories)} {filter_status} proposal(s):\n")
    for m in memories:
        mid = str(m["id"])[:8]
        created = m["created_at"].strftime("%Y-%m-%d %H:%M")
        flags = m["injection_flags"] or []
        flag_str = f"  [FLAGGED: {', '.join(flags)}]" if flags else ""
        tags = m["tags"] or []
        tag_str = f"  tags={tags}" if tags else ""
        preview = m["content"][:120].replace("\n", " ")
        if len(m["content"]) > 120:
            preview += "..."
        click.echo(
            f"  {mid}...  {created}  salience={m['salience']:.1f}"
            f"{tag_str}{flag_str}\n    {preview}\n"
        )


@proposals.command("decide")
@click.argument("memory_id")
@click.option(
    "--action",
    required=True,
    type=click.Choice(["confirm", "reject"], case_sensitive=False),
)
@click.option("--note", default=None, help="Optional note")
def proposals_decide(memory_id: str, action: str, note: str | None):
    """Confirm or reject a memory proposal."""

    async def _decide():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id, status FROM memories WHERE id = :mid"),
                {"mid": memory_id},
            )
            result = row.mappings().first()
            if result is None:
                click.echo("Error: memory not found.", err=True)
                raise SystemExit(1)
            if result["status"] != "pending":
                click.echo(f"Error: memory is '{result['status']}', not pending.", err=True)
                raise SystemExit(1)

            new_status = "confirmed" if action == "confirm" else "rejected"
            await session.execute(
                text("UPDATE memories SET status = :st WHERE id = :mid"),
                {"st": new_status, "mid": memory_id},
            )
            await session.commit()
            return new_status

    status = _run(_decide())
    click.echo(f"Memory {memory_id[:8]}... {status}.")


# ── Memory ────────────────────────────────────────────────────


@cli.group()
def memory():
    """Inspect and manage memories."""


@memory.command("search")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("-k", "k", default=5, help="Number of results")
@click.argument("query")
def memory_search(soul_slug: str, k: int, query: str):
    """Semantic search through confirmed memories."""

    async def _search():
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
            dek = decrypt_dek(bytes(dek_row.mappings().first()["dek_encrypted"]))

            query_embedding = await embed_text(query)
            embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

            rows = await session.execute(
                text(
                    "SELECT id, content_encrypted, content_nonce, created_at, "
                    "salience, tags, embedding <=> CAST(:qemb AS vector) AS distance "
                    "FROM memories "
                    "WHERE soul_id = :sid AND status = 'confirmed' "
                    "ORDER BY embedding <=> CAST(:qemb AS vector) "
                    "LIMIT :k"
                ),
                {"sid": str(soul_id), "qemb": embedding_str, "k": k},
            )
            results = []
            for m in rows.mappings().all():
                plaintext = decrypt_content(
                    bytes(m["content_encrypted"]),
                    bytes(m["content_nonce"]),
                    dek,
                )
                results.append({**m, "content": plaintext})
            return results

    memories = _run(_search())
    if not memories:
        click.echo("No matching memories.")
        return

    for m in memories:
        mid = str(m["id"])[:8]
        created = m["created_at"].strftime("%Y-%m-%d")
        dist = f"dist={m['distance']:.3f}" if m.get("distance") is not None else ""
        click.echo(
            f"  {mid}...  {created}  salience={m['salience']:.1f}  {dist}"
        )
        click.echo(f"    {m['content'][:200]}")
        click.echo()


@memory.command("list")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--recent", "days", default=7, help="Number of days to look back")
@click.option("--status", "filter_status", default="confirmed", help="Filter by status")
def memory_list_cmd(soul_slug: str, days: int, filter_status: str):
    """List recent memories for a soul."""

    async def _list():
        from datetime import timedelta as td
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
            dek = decrypt_dek(bytes(dek_row.mappings().first()["dek_encrypted"]))

            cutoff = datetime.now(timezone.utc) - td(days=days)
            rows = await session.execute(
                text(
                    "SELECT id, content_encrypted, content_nonce, created_at, "
                    "salience, tags, status "
                    "FROM memories WHERE soul_id = :sid AND status = :st "
                    "AND created_at >= :cutoff "
                    "ORDER BY created_at DESC LIMIT 50"
                ),
                {"sid": str(soul_id), "st": filter_status, "cutoff": cutoff},
            )
            results = []
            for m in rows.mappings().all():
                plaintext = decrypt_content(
                    bytes(m["content_encrypted"]),
                    bytes(m["content_nonce"]),
                    dek,
                )
                results.append({**m, "content": plaintext})
            return results

    memories = _run(_list())
    if not memories:
        click.echo("No memories found.")
        return

    click.echo(f"{len(memories)} memories (last {days} days):\n")
    for m in memories:
        mid = str(m["id"])[:8]
        created = m["created_at"].strftime("%Y-%m-%d %H:%M")
        preview = m["content"][:120].replace("\n", " ")
        if len(m["content"]) > 120:
            preview += "..."
        click.echo(f"  {mid}...  {created}  salience={m['salience']:.1f}")
        click.echo(f"    {preview}\n")


@memory.command("forget")
@click.argument("memory_id")
@click.confirmation_option(prompt="Mark this memory for forgetting?")
def memory_forget(memory_id: str):
    """Mark a confirmed memory as forgotten."""

    async def _forget():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id, status FROM memories WHERE id = :mid"),
                {"mid": memory_id},
            )
            result = row.mappings().first()
            if result is None:
                click.echo("Error: memory not found.", err=True)
                raise SystemExit(1)
            if result["status"] not in ("confirmed", "pending"):
                click.echo(f"Error: memory is '{result['status']}'.", err=True)
                raise SystemExit(1)

            await session.execute(
                text("UPDATE memories SET status = 'forgotten' WHERE id = :mid"),
                {"mid": memory_id},
            )
            await session.commit()

    _run(_forget())
    click.echo(f"Memory {memory_id[:8]}... marked as forgotten.")


# ── Fact ──────────────────────────────────────────────────────


@cli.group()
def fact():
    """Manage soul facts (structured knowledge)."""


@fact.command("set")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--category", required=True, help="Fact category (e.g. 'user_profile')")
@click.option("--key", required=True, help="Fact key (e.g. 'employer')")
@click.option("--confidence", default=1.0, help="Confidence score 0.0-1.0")
@click.argument("value")
def fact_set(soul_slug: str, category: str, key: str, confidence: float, value: str):
    """Set (upsert) a fact for a soul.

    VALUE is the fact content as a string.
    """
    import re

    pattern = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")
    if not pattern.match(category):
        click.echo(f"Error: invalid category format: '{category}'", err=True)
        raise SystemExit(1)
    if not pattern.match(key):
        click.echo(f"Error: invalid key format: '{key}'", err=True)
        raise SystemExit(1)

    async def _set():
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT id, tenant_id FROM souls WHERE slug = :slug"),
                {"slug": soul_slug},
            )
            soul_row = row.mappings().first()
            if not soul_row:
                click.echo(f"Error: soul '{soul_slug}' not found.", err=True)
                raise SystemExit(1)

            soul_id = soul_row["id"]
            tenant_id = soul_row["tenant_id"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek_result = dek_row.mappings().first()
            if not dek_result:
                click.echo("Error: no encryption key for this soul.", err=True)
                raise SystemExit(1)

            dek = decrypt_dek(bytes(dek_result["dek_encrypted"]))
            ct, nonce = encrypt_content(value, dek)

            await session.execute(
                text("""
                    INSERT INTO facts
                        (tenant_id, soul_id, category, key, value_encrypted,
                         value_nonce, confidence, status, updated_at)
                    VALUES
                        (:tid, :sid, :cat, :key, :ct, :nonce, :conf, 'active', NOW())
                    ON CONFLICT (tenant_id, soul_id, category, key)
                    DO UPDATE SET
                        value_encrypted = EXCLUDED.value_encrypted,
                        value_nonce = EXCLUDED.value_nonce,
                        confidence = EXCLUDED.confidence,
                        status = 'active',
                        updated_at = NOW()
                """),
                {
                    "tid": str(tenant_id),
                    "sid": str(soul_id),
                    "cat": category,
                    "key": key,
                    "ct": ct,
                    "nonce": nonce,
                    "conf": confidence,
                },
            )
            await session.commit()

    _run(_set())
    click.echo(f"Fact set: {category}/{key}")


@fact.command("list")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--category", default=None, help="Filter by category")
def fact_list(soul_slug: str, category: str | None):
    """List facts for a soul."""

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

            soul_id = soul_row["id"]

            dek_row = await session.execute(
                text("SELECT dek_encrypted FROM soul_keys WHERE soul_id = :sid"),
                {"sid": str(soul_id)},
            )
            dek = decrypt_dek(bytes(dek_row.mappings().first()["dek_encrypted"]))

            if category:
                rows = await session.execute(
                    text(
                        "SELECT id, category, key, value_encrypted, value_nonce, "
                        "confidence, updated_at "
                        "FROM facts WHERE soul_id = :sid AND status = 'active' "
                        "AND category = :cat ORDER BY category, key"
                    ),
                    {"sid": str(soul_id), "cat": category},
                )
            else:
                rows = await session.execute(
                    text(
                        "SELECT id, category, key, value_encrypted, value_nonce, "
                        "confidence, updated_at "
                        "FROM facts WHERE soul_id = :sid AND status = 'active' "
                        "ORDER BY category, key"
                    ),
                    {"sid": str(soul_id)},
                )

            results = []
            for f in rows.mappings().all():
                plaintext = decrypt_content(
                    bytes(f["value_encrypted"]),
                    bytes(f["value_nonce"]),
                    dek,
                )
                results.append({**f, "value": plaintext})
            return results

    facts = _run(_list())
    if not facts:
        click.echo("No facts found.")
        return

    click.echo(f"{len(facts)} fact(s):\n")
    for f in facts:
        preview = f["value"][:80].replace("\n", " ")
        if len(f["value"]) > 80:
            preview += "..."
        click.echo(
            f"  {f['category']:20s}  {f['key']:20s}  "
            f"conf={f['confidence']:.1f}  {preview}"
        )


@fact.command("get")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--category", required=True, help="Fact category")
@click.option("--key", required=True, help="Fact key")
def fact_get(soul_slug: str, category: str, key: str):
    """Get a single fact's value."""

    async def _get():
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
            dek = decrypt_dek(bytes(dek_row.mappings().first()["dek_encrypted"]))

            fact_row = await session.execute(
                text(
                    "SELECT value_encrypted, value_nonce, confidence, updated_at "
                    "FROM facts WHERE soul_id = :sid AND category = :cat "
                    "AND key = :key AND status = 'active'"
                ),
                {"sid": str(soul_id), "cat": category, "key": key},
            )
            result = fact_row.mappings().first()
            if not result:
                click.echo(f"Error: no active fact '{category}/{key}'.", err=True)
                raise SystemExit(1)

            plaintext = decrypt_content(
                bytes(result["value_encrypted"]),
                bytes(result["value_nonce"]),
                dek,
            )
            return plaintext, result["confidence"], result["updated_at"]

    value, confidence, updated_at = _run(_get())
    click.echo(f"  {category}/{key}  (confidence={confidence:.1f}, updated={updated_at})")
    click.echo(f"  {value}")


@fact.command("remove")
@click.option("--soul", "soul_slug", required=True, help="Soul slug")
@click.option("--category", required=True, help="Fact category")
@click.option("--key", required=True, help="Fact key")
@click.confirmation_option(prompt="Remove this fact?")
def fact_remove(soul_slug: str, category: str, key: str):
    """Soft-delete a fact."""

    async def _remove():
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

            result = await session.execute(
                text(
                    "SELECT id FROM facts WHERE soul_id = :sid "
                    "AND category = :cat AND key = :key AND status = 'active'"
                ),
                {"sid": str(soul_id), "cat": category, "key": key},
            )
            fact_row = result.mappings().first()
            if not fact_row:
                click.echo(f"Error: no active fact '{category}/{key}'.", err=True)
                raise SystemExit(1)

            await session.execute(
                text(
                    "UPDATE facts SET status = 'deleted', updated_at = NOW() "
                    "WHERE id = :fid"
                ),
                {"fid": str(fact_row["id"])},
            )
            await session.commit()

    _run(_remove())
    click.echo(f"Fact '{category}/{key}' removed.")


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
