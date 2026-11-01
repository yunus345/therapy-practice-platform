"""ASGI application entry point."""

from fastapi import FastAPI

from app.core.config import Environment, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = settings.environment is not Environment.PRODUCTION
    return FastAPI(
        title="Seans API",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
