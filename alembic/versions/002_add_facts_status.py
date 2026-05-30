"""Add status column to facts table for soft-delete.

Revision ID: 002_add_facts_status
Revises: 001_baseline
Create Date: 2026-05-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002_add_facts_status"
down_revision = "001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "facts",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "ck_facts_status",
        "facts",
        "status IN ('active', 'deleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_facts_status", "facts", type_="check")
    op.drop_column("facts", "status")
