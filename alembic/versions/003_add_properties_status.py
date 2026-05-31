"""Add status column to soul_properties for soft-delete.

Revision ID: 003_add_properties_status
Revises: 002_add_facts_status
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003_add_properties_status"
down_revision = "002_add_facts_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "soul_properties",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "ck_soul_properties_status",
        "soul_properties",
        "status IN ('active', 'deleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_soul_properties_status", "soul_properties", type_="check")
    op.drop_column("soul_properties", "status")
