"""Alembic env.py – async configuration for Soulservice."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from soulservice.core.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models so Alembic sees them
from soulservice.models.tenant import Tenant, User  # noqa: F401, E402
from soulservice.models.soul import Soul, SoulKey, SoulSelfCore, SoulSelfCoreHistory  # noqa: F401, E402
from soulservice.models.token import ApiToken  # noqa: F401, E402
from soulservice.models.memory import Memory  # noqa: F401, E402
from soulservice.models.fact import Fact  # noqa: F401, E402
from soulservice.models.property import SoulProperty  # noqa: F401, E402
from soulservice.models.proposal import Proposal  # noqa: F401, E402
from soulservice.models.audit import AuditLog  # noqa: F401, E402

from sqlmodel import SQLModel

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = settings.database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
