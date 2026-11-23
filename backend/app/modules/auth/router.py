import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.config import Settings
from app.core.rate_limit import RateLimit
from app.modules.auth.csrf import CSRF_COOKIE, SESSION_COOKIE, issue_csrf_token
from app.modules.auth.dependencies import (
    AuthServiceDep,
    Public,
    RequestMetaDep,
    SessionContextDep,
    SettingsDep,
    current_user,
    require_mfa,
    require_session_roles,
    session_rate_limit_key,
)
from app.modules.auth.models import AuthSession
from app.modules.auth.schemas import (
    AcceptedResponse,
    CsrfResponse,
    LoginRequest,
    MeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    SessionResponse,
    SessionStateResponse,
    TokenRequest,
    TwoFactorCodeRequest,
    TwoFactorDisableRequest,
    TwoFactorEnabledResponse,
    TwoFactorSetupResponse,
    TwoFactorVerifyRequest,
)
from app.modules.auth.service import IssuedSession, SessionContext
from app.modules.users.actor import STAFF_ROLES, Actor

router = APIRouter(prefix="/auth", tags=["auth"])

FIVE_MINUTES = 300
ONE_HOUR = 3600

AuthenticatedActor = Annotated[Actor, Depends(require_mfa)]
SessionActor = Annotated[Actor, Depends(current_user)]
StaffSession = Depends(require_session_roles(*STAFF_ROLES))
TwoFactorRateLimit = Depends(RateLimit("auth.2fa", 5, FIVE_MINUTES, key=session_rate_limit_key))


def _set_cookie(
    response: Response,
    settings: Settings,
    name: str,
    value: str,
    *,
    http_only: bool,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=settings.session_absolute_lifetime_hours * ONE_HOUR,
        httponly=http_only,
        secure=settings.cookies_secure,
        samesite="lax",
    )


def _set_csrf_cookie(response: Response, settings: Settings, session_token: str | None) -> str:
    csrf_token = issue_csrf_token(settings, session_token)
    _set_cookie(response, settings, CSRF_COOKIE, csrf_token, http_only=False)
    return csrf_token


def _set_session_cookies(
    response: Response, settings: Settings, issued: IssuedSession
) -> SessionStateResponse:
    _set_cookie(response, settings, SESSION_COOKIE, issued.token, http_only=True)
    return SessionStateResponse(
        csrf_token=_set_csrf_cookie(response, settings, issued.token),
        mfa_required=issued.context.mfa_pending,
        two_factor_setup_required=issued.context.setup_required,
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, secure=settings.cookies_secure, samesite="lax")


def _session_response(row: AuthSession, context: SessionContext) -> SessionResponse:
    return SessionResponse(
        id=row.id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        ip_address=str(row.ip_address) if row.ip_address else None,
        user_agent=row.user_agent,
        is_current=row.id == context.auth_session.id,
    )


@router.get("/csrf", dependencies=[Public])
async def get_csrf_token(
    request: Request, response: Response, settings: SettingsDep
) -> CsrfResponse:
    session_token = request.cookies.get(SESSION_COOKIE)
    return CsrfResponse(csrf_token=_set_csrf_cookie(response, settings, session_token))


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Public, Depends(RateLimit("auth.register", 10, ONE_HOUR))],
)
async def register(
    body: RegisterRequest, service: AuthServiceDep, meta: RequestMetaDep
) -> AcceptedResponse:
    await service.register_client(
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        meta=meta,
    )
    return AcceptedResponse()


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Public, Depends(RateLimit("auth.verify_email", 10, FIVE_MINUTES))],
)
async def verify_email(body: TokenRequest, service: AuthServiceDep, meta: RequestMetaDep) -> None:
    await service.verify_email(body.token, meta)


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RateLimit("auth.resend_verification", 5, ONE_HOUR))],
)
async def resend_verification_email(
    actor: AuthenticatedActor, service: AuthServiceDep
) -> AcceptedResponse:
    await service.resend_verification_email(actor)
    return AcceptedResponse()


@router.post(
    "/login",
    dependencies=[Public, Depends(RateLimit("auth.login", 10, FIVE_MINUTES))],
)
async def login(
    body: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    meta: RequestMetaDep,
) -> SessionStateResponse:
    issued = await service.login(body.email, body.password, meta)
    return _set_session_cookies(response, settings, issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    _: SessionActor,
    context: SessionContextDep,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    meta: RequestMetaDep,
) -> None:
    await service.logout(context, meta)
    _clear_session_cookies(response, settings)


@router.get("/me")
async def get_me(_: SessionActor, context: SessionContextDep) -> MeResponse:
    user = context.user
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        email_verified=user.email_verified_at is not None,
        totp_enabled=user.totp_enabled,
        mfa_required=context.mfa_pending,
        two_factor_setup_required=context.setup_required,
    )


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Public, Depends(RateLimit("auth.password_reset", 5, FIVE_MINUTES * 3))],
)
async def request_password_reset(
    body: PasswordResetRequest, service: AuthServiceDep, meta: RequestMetaDep
) -> AcceptedResponse:
    await service.request_password_reset(body.email, meta)
    return AcceptedResponse()


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Public, Depends(RateLimit("auth.password_reset_confirm", 10, FIVE_MINUTES * 3))],
)
async def confirm_password_reset(
    body: PasswordResetConfirm, service: AuthServiceDep, meta: RequestMetaDep
) -> None:
    await service.confirm_password_reset(body.token, body.new_password, meta)


@router.post("/2fa/setup", dependencies=[StaffSession])
async def start_two_factor_setup(
    actor: SessionActor, service: AuthServiceDep
) -> TwoFactorSetupResponse:
    setup = await service.start_two_factor_setup(actor)
    return TwoFactorSetupResponse(provisioning_uri=setup.provisioning_uri)


@router.post(
    "/2fa/enable",
    dependencies=[StaffSession, TwoFactorRateLimit],
)
async def enable_two_factor(
    body: TwoFactorCodeRequest,
    context: SessionContextDep,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    meta: RequestMetaDep,
) -> TwoFactorEnabledResponse:
    enabled = await service.enable_two_factor(context, body.code, meta)
    state = _set_session_cookies(response, settings, enabled.session)
    return TwoFactorEnabledResponse(**state.model_dump(), recovery_codes=enabled.recovery_codes)


@router.post("/2fa/verify", dependencies=[TwoFactorRateLimit])
async def verify_two_factor(
    body: TwoFactorVerifyRequest,
    _: SessionActor,
    context: SessionContextDep,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    meta: RequestMetaDep,
) -> SessionStateResponse:
    issued = await service.verify_two_factor(
        context, code=body.code, recovery_code=body.recovery_code, meta=meta
    )
    return _set_session_cookies(response, settings, issued)


@router.post(
    "/2fa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[TwoFactorRateLimit],
)
async def disable_two_factor(
    body: TwoFactorDisableRequest,
    _: AuthenticatedActor,
    context: SessionContextDep,
    service: AuthServiceDep,
    meta: RequestMetaDep,
) -> None:
    await service.disable_two_factor(context, body.password, body.code, meta)


@router.get("/sessions")
async def list_sessions(
    actor: AuthenticatedActor, context: SessionContextDep, service: AuthServiceDep
) -> list[SessionResponse]:
    rows = await service.list_sessions(actor)
    return [_session_response(row, context) for row in rows]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    actor: AuthenticatedActor,
    context: SessionContextDep,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    meta: RequestMetaDep,
) -> None:
    await service.revoke_session(actor, session_id, meta)
    if session_id == context.auth_session.id:
        _clear_session_cookies(response, settings)
