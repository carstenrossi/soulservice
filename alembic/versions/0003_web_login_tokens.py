"""Web UI magic-link login tokens (admin auth).

Revision ID: 0003_web_login_tokens
Revises: 0002_force_rls
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op

revision = "0003_web_login_tokens"
down_revision = "0002_force_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE web_login_tokens (
          token_hash TEXT PRIMARY KEY,
          email TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          expires_at TIMESTAMPTZ NOT NULL,
          used_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX web_login_tokens_expires_idx ON web_login_tokens (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS web_login_tokens CASCADE")
