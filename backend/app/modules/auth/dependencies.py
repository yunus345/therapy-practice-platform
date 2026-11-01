"""Route dependencies for authentication and access policies.

Every route must declare a policy: `Public`, `current_user`, `require_mfa`,
`require_roles(...)` or `require_session_roles(...)`. `assert_routes_declare_access`
stops the application at startup if a route has none, which makes deny by default a
structural guarantee.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.database import WriteSession
from app.core.rate_limit import client_ip
from app.core.security import hash_token
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.auth.csrf import SESSION_COOKIE
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import (
    AuthService,
    MfaRequiredError,
    NotAuthenticatedError,
    RequestMeta,
    SessionContext,
    TwoFactorSetupRequiredError,
)
from app.modules.clients.repository import ClientRepository
from app.modules.clients.service import ClientService
from app.modules.consent.repository import ConsentRepository
from app.modules.consent.service import ConsentService
from app.modules.notifications.service import BackgroundEmailQueue, EmailQueue
from app.modules.users.actor import Actor
from app.modules.users.models import UserRole
from app.modules.users.repository import UserRepository
from app.modules.users.service import UserService

ACCESS_POLICY_ATTRIBUTE = "__access_policy__"


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ClockDep = Annotated[Clock, Depends(get_clock)]


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=client_ip(request) if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )


RequestMetaDep = Annotated[RequestMeta, Depends(get_request_meta)]


def session_rate_limit_key(request: Request) -> str:
    """Count per session, because colleagues in one office share an IP address."""
    token = request.cookies.get(SESSION_COOKIE)
    return hash_token(token) if token else client_ip(request)


def get_email_queue(request: Request, background_tasks: BackgroundTasks) -> EmailQueue:
    return BackgroundEmailQueue(background_tasks, request.app.state.mailer)


def build_auth_service(
    session: AsyncSession, emails: EmailQueue, settings: Settings, clock: Clock
) -> AuthService:
    return AuthService(
        AuthRepository(session),
        UserService(UserRepository(session), clock),
        ClientService(ClientRepository(session)),
        ConsentService(ConsentRepository(session)),
        AuditService(AuditRepository(session)),
        emails,
        settings,
        clock,
    )


def get_auth_service(
    session: WriteSession,
    emails: Annotated[EmailQueue, Depends(get_email_queue)],
    settings: SettingsDep,
    clock: ClockDep,
) -> AuthService:
    return build_auth_service(session, emails, settings, clock)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_session_context(request: Request, service: AuthServiceDep) -> SessionContext:
    """Resolve the session in the request's transaction, as services change these rows."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise NotAuthenticatedError
    return await service.resolve_session(token)


SessionContextDep = Annotated[SessionContext, Depends(get_session_context)]


def _declare_policy[Policy: Callable[..., Awaitable[Any]]](name: str, policy: Policy) -> Policy:
    setattr(policy, ACCESS_POLICY_ATTRIBUTE, name)
    return policy


async def _public() -> None:
    """Marks a route as intentionally open to anonymous callers."""


_declare_policy("public", _public)
Public = Depends(_public)


async def current_user(context: SessionContextDep) -> Actor:
    """Any valid session, including one still waiting for 2FA."""
    return context.actor


_declare_policy("session", current_user)


async def require_mfa(context: SessionContextDep) -> Actor:
    """A fully authenticated session: 2FA passed, and set up if it is mandatory."""
    if context.mfa_pending:
        raise MfaRequiredError
    if context.setup_required:
        raise TwoFactorSetupRequiredError
    return context.actor


_declare_policy("authenticated", require_mfa)


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[Actor]]:
    """A fully authenticated session with one of the given roles."""

    async def dependency(actor: Annotated[Actor, Depends(require_mfa)]) -> Actor:
        actor.require_role(*roles)
        return actor

    return _declare_policy(f"roles:{','.join(sorted(roles))}", dependency)


def require_session_roles(*roles: UserRole) -> Callable[..., Awaitable[Actor]]:
    """A valid session with one of the given roles, before 2FA requirements apply."""

    async def dependency(actor: Annotated[Actor, Depends(current_user)]) -> Actor:
        actor.require_role(*roles)
        return actor

    return _declare_policy(f"session_roles:{','.join(sorted(roles))}", dependency)


def route_policies(dependant: Dependant) -> set[str]:
    policies: set[str] = set()
    policy = getattr(dependant.call, ACCESS_POLICY_ATTRIBUTE, None)
    if policy is not None:
        policies.add(policy)
    for dependency in dependant.dependencies:
        policies |= route_policies(dependency)
    return policies


def api_routes(app: FastAPI) -> list[tuple[str, APIRoute]]:
    """Return the full path and route of every API route, including nested routers."""
    return [
        (path, context.original_route)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and (path := context.path) is not None
        and path.startswith("/api/")
    ]


def assert_routes_declare_access(app: FastAPI) -> None:
    routes = api_routes(app)
    if not routes:
        raise RuntimeError("No API routes found, so access policies cannot be verified.")

    undeclared = [path for path, route in routes if not route_policies(route.dependant)]
    if undeclared:
        raise RuntimeError(f"Routes without an access policy: {', '.join(undeclared)}")
