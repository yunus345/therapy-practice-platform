from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Environment, Settings, get_settings
from app.main import create_app

STRONG_SECRET = "x" * 32

SettingsFactory = Callable[..., Settings]


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        test_url = get_settings().test_database_url
        assert test_url is not None, "TEST_DATABASE_URL must be set"
        values: dict[str, Any] = {
            "environment": Environment.DEV,
            "database_url": test_url.get_secret_value(),
            "migration_database_url": test_url.get_secret_value(),
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
