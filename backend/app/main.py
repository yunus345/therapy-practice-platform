"""ASGI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.core.clock import utcnow
from app.core.config import Environment, Settings, get_settings
from app.core.database import Database
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middleware
from app.core.rate_limit import InMemoryRateLimitBackend
from app.modules.auth.dependencies import assert_routes_declare_access
from app.modules.notifications.service import SmtpMailer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.database.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    docs_enabled = settings.environment is not Environment.PRODUCTION
    app = FastAPI(
        title="Seans API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.database = Database(settings.database_url.get_secret_value())
    app.state.rate_limit_backend = InMemoryRateLimitBackend()
    app.state.clock = utcnow
    app.state.mailer = SmtpMailer(settings)

    register_error_handlers(app)
    register_middleware(app, settings)
    app.include_router(api_router)
    assert_routes_declare_access(app)
    return app
