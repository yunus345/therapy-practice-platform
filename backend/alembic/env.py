"""Alembic environment. Migrations run as the owner role, never the application role.

Callers can set the `database_url` config attribute to migrate another database. Tests
use this for the test database.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, make_url, pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings
from app.models import Base

config = context.config
settings = get_settings()

database_url: str | None = config.attributes.get("database_url")
if database_url is None:
    database_url = settings.migration_database_url.get_secret_value()
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)

# Migrations grant table privileges to this role.
config.attributes["app_role"] = make_url(settings.database_url.get_secret_value()).username


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    assert database_url is not None  # noqa: S101
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported.")

asyncio.run(run_migrations_online())
