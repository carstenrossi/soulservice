"""Force row level security on sensitive tables (defense-in-depth).

By default RLS does not apply to a table's owner. FORCE ROW LEVEL SECURITY
makes the owner subject to the policies too. Note: a superuser (or a role with
BYPASSRLS) still bypasses RLS even with FORCE; this is future-proofing for the
case of a non-privileged table owner.

Revision ID: 0002_force_rls
Revises: 0001_initial_schema
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op

revision = "0002_force_rls"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

_RLS_TABLES: list[str] = [
    "memories",
    "facts",
    "soul_properties",
    "soul_adaptations",
    "proposals",
    "soul_keys",
    "soul_self_cores",
    "soul_self_core_history",
]


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
