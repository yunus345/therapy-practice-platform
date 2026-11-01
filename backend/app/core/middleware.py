"""Pure ASGI middleware.

These run around Starlette's own error handling, so that responses for unhandled
errors also carry the request ID and security headers.
"""

import time
import uuid

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Environment, Settings
from app.core.errors import error_response

logger = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
CSRF_HEADER = "X-CSRF-Token"

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
# Swagger UI loads its assets from a CDN. /docs exists only outside production.
DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect"})


class RequestContextMiddleware:
    """Assigns a request ID and writes one access log line per request.

    The ID is always generated here, because an inbound value could inject content
    into logs. Only the route template is logged, because raw paths and query strings
    can contain personal data or tokens.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        status_code = 500
        started_at = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            finally:
                route = scope.get("route")
                logger.info(
                    "request_completed",
                    method=scope["method"],
                    route=getattr(route, "path", "unmatched"),
                    status_code=status_code,
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool, docs_enabled: bool) -> None:
        self.app = app
        self.hsts = hsts
        self.docs_enabled = docs_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_docs = self.docs_enabled and scope["path"] in DOCS_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
                headers["Content-Security-Policy"] = DOCS_CSP if is_docs else API_CSP
                # Responses can contain personal data and must not stay in caches.
                headers["Cache-Control"] = "no-store"
                if self.hsts:
                    headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class UnhandledErrorMiddleware:
    """Turns any unhandled exception into a generic 500 without internals."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            response_started = response_started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as error:
            logger.error("unhandled_error", exc_info=error)
            if response_started:
                raise
            response = error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")
            await response(scope, receive, send)


def register_middleware(app: FastAPI, settings: Settings) -> None:
    is_production = settings.environment is Environment.PRODUCTION
    # The last middleware added is the outermost one.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(
        SecurityHeadersMiddleware, hsts=is_production, docs_enabled=not is_production
    )
    app.add_middleware(RequestContextMiddleware)
