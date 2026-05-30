"""Baseline: stamp existing schema from init.sql.

This migration does not execute any DDL. It marks the database as
being at the initial state defined by infra/init.sql.

Revision ID: 001_baseline
Create Date: 2026-05-30
"""

from __future__ import annotations

from alembic import op  # noqa: F401

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
