"""CSRF protection with a signed double-submit token.

The token is signed and bound to the session cookie, so that an attacker who can plant
a cookie from a sibling subdomain cannot forge a matching pair. Requests without a
session use the binding `anonymous`, which covers login and registration.
"""

from fastapi import Request

from app.core.config import Settings
from app.core.errors import AppError
from app.core.middleware import CSRF_HEADER
from app.core.security import create_csrf_token, hash_token, verify_csrf_token

SESSION_COOKIE = "seans_session"
CSRF_COOKIE = "seans_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ANONYMOUS_BINDING = "anonymous"


class CsrfInvalidError(AppError):
    status_code = 403
    code = "CSRF_INVALID"
    message = "The security token is missing or invalid."


def csrf_binding(session_token: str | None) -> str:
    return hash_token(session_token) if session_token else ANONYMOUS_BINDING


def issue_csrf_token(settings: Settings, session_token: str | None) -> str:
    return create_csrf_token(settings.csrf_secret.get_secret_value(), csrf_binding(session_token))


async def verify_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return

    settings: Settings = request.app.state.settings
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or cookie != header:
        raise CsrfInvalidError

    binding = csrf_binding(request.cookies.get(SESSION_COOKIE))
    if not verify_csrf_token(settings.csrf_secret.get_secret_value(), binding, header):
        raise CsrfInvalidError
