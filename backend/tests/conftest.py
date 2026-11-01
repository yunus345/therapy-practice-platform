from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import make_url, pool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from alembic import command
from app.core.config import REPO_ROOT, Environment, Settings, get_settings
from app.main import create_app

STRONG_SECRET = "x" * 32

SettingsFactory = Callable[..., Settings]


def test_database_url() -> str:
    url = get_settings().test_database_url
    assert url is not None, "TEST_DATABASE_URL must be set"
    return url.get_secret_value()


def alembic_config() -> Config:
    """Alembic config for the test database, connecting as the owner role."""
    owner_url = make_url(get_settings().migration_database_url.get_secret_value()).set(
        database=make_url(test_database_url()).database
    )
    config = Config(REPO_ROOT / "backend" / "alembic.ini")
    config.attributes["database_url"] = owner_url.render_as_string(hide_password=False)
    return config


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    command.upgrade(alembic_config(), "head")
    yield


@pytest.fixture
async def db_session(migrated_database: None) -> AsyncIterator[AsyncSession]:
    """Session on the restricted role, rolled back after the test."""
    engine = create_async_engine(test_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        yield session
        await session.close()
        await transaction.rollback()
    await engine.dispose()


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": Environment.DEV,
            "database_url": test_database_url(),
            "migration_database_url": test_database_url(),
            "csrf_secret": STRONG_SECRET,
            "totp_encryption_key": STRONG_SECRET,
        }
        return Settings.model_validate(values | overrides)

    return factory


@pytest.fixture
async def app(make_settings: SettingsFactory) -> AsyncIterator[FastAPI]:
    application = create_app(make_settings())
    yield application
    await application.state.database.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
