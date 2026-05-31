"""Grant the restricted app role access to web_login_tokens.

The admin web UI no longer runs as the DB owner; it uses the restricted
``soulservice_app`` role (same as the MCP runtime). The magic-link login path
therefore needs DML rights on the ``web_login_tokens`` table. This table has no
tenant/soul columns and no RLS policy, so a plain grant is sufficient.

Revision ID: 0004_web_admin_grants
Revises: 0003_web_login_tokens
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op

revision = "0004_web_admin_grants"
down_revision = "0003_web_login_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON web_login_tokens TO soulservice_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON web_login_tokens FROM soulservice_app"
    )
