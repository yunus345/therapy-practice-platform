from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import InMemoryRateLimitBackend
from app.modules.audit.models import AuditLog
from app.modules.auth.csrf import CSRF_COOKIE, SESSION_COOKIE
from app.modules.auth.dependencies import require_roles
from app.modules.auth.models import AuthRecoveryCode, AuthSession
from app.modules.users.models import User, UserRole
from tests.conftest import (
    DEFAULT_PASSWORD,
    ClientFactory,
    FakeClock,
    UserFactory,
    log_in,
    replace_cookie,
)


def totp_code(uri: str, clock: FakeClock, step_offset: int = 0) -> str:
    secret = parse_qs(urlparse(uri).query)["secret"][0]
    step = int(clock().timestamp()) // 30 + step_offset
    return pyotp.TOTP(secret).generate_otp(step)


async def enable_two_factor(client: httpx.AsyncClient, clock: FakeClock) -> tuple[str, list[str]]:
    uri = (await client.post("/auth/2fa/setup")).json()["provisioning_uri"]
    response = await client.post("/auth/2fa/enable", json={"code": totp_code(uri, clock)})
    assert response.status_code == 200
    clock.advance(seconds=90)
    return uri, response.json()["recovery_codes"]


@pytest.fixture
async def staff(
    new_client: ClientFactory, make_user: UserFactory, clock: FakeClock
) -> tuple[User, str, list[str]]:
    """A psychologist with 2FA enabled, and their secret URI and recovery codes."""
    user = await make_user(UserRole.PSYCHOLOGIST)
    client = await new_client()
    await log_in(client, user.email)
    uri, codes = await enable_two_factor(client, clock)
    return user, uri, codes


async def test_setup_stores_secret_encrypted_and_does_not_enable_yet(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user(UserRole.PSYCHOLOGIST)
    await log_in(api, user.email)

    response = await api.post("/auth/2fa/setup")

    assert response.status_code == 200
    uri = response.json()["provisioning_uri"]
    assert uri.startswith("otpauth://totp/")
    assert "Seans" in uri
    secret = parse_qs(urlparse(uri).query)["secret"][0]
    await db_session.refresh(user)
    assert user.totp_secret_encrypted is not None
    assert secret not in user.totp_secret_encrypted
    assert user.totp_enabled is False


async def test_enable_requires_a_valid_code(api: httpx.AsyncClient, make_user: UserFactory) -> None:
    user = await make_user(UserRole.PSYCHOLOGIST)
    await log_in(api, user.email)
    await api.post("/auth/2fa/setup")

    response = await api.post("/auth/2fa/enable", json={"code": "000000"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CODE"


async def test_enable_returns_recovery_codes_and_rotates_the_session(
    api: httpx.AsyncClient,
    make_user: UserFactory,
    clock: FakeClock,
    db_session: AsyncSession,
) -> None:
    user = await make_user(UserRole.ADMIN)
    await log_in(api, user.email)
    before = api.cookies[SESSION_COOKIE]

    _, codes = await enable_two_factor(api, clock)

    assert len(codes) == 8
    assert api.cookies[SESSION_COOKIE] != before
    stored = (await db_session.scalars(select(AuthRecoveryCode))).all()
    assert len(stored) == 8
    assert not {row.code_hash for row in stored} & set(codes)
    replace_cookie(api, SESSION_COOKIE, before)
    assert (await api.get("/auth/me")).status_code == 401


async def test_client_role_cannot_use_two_factor(
    api: httpx.AsyncClient, make_user: UserFactory
) -> None:
    await log_in(api, (await make_user(UserRole.CLIENT)).email)

    assert (await api.post("/auth/2fa/setup")).status_code == 403


async def test_login_with_2fa_starts_unverified_and_blocks_other_routes(
    api: httpx.AsyncClient, staff: tuple[User, str, list[str]]
) -> None:
    user, _, _ = staff

    response = await log_in(api, user.email)

    assert response.json()["mfa_required"] is True
    blocked = await api.get("/auth/sessions")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "MFA_REQUIRED"
    assert (await api.get("/auth/me")).json()["mfa_required"] is True


async def test_verify_elevates_the_session_and_rotates_tokens(
    api: httpx.AsyncClient,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
) -> None:
    user, uri, _ = staff
    await log_in(api, user.email)
    session_before = api.cookies[SESSION_COOKIE]
    csrf_before = api.cookies[CSRF_COOKIE]

    response = await api.post("/auth/2fa/verify", json={"code": totp_code(uri, clock)})

    assert response.status_code == 200
    assert response.json()["mfa_required"] is False
    assert api.cookies[SESSION_COOKIE] != session_before
    assert api.cookies[CSRF_COOKIE] != csrf_before
    assert (await api.get("/auth/sessions")).status_code == 200


async def test_totp_code_cannot_be_replayed(
    new_client: ClientFactory,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
) -> None:
    user, uri, _ = staff
    code = totp_code(uri, clock)
    first = await new_client()
    second = await new_client()
    await log_in(first, user.email)
    await log_in(second, user.email)

    assert (await first.post("/auth/2fa/verify", json={"code": code})).status_code == 200
    assert (await second.post("/auth/2fa/verify", json={"code": code})).status_code == 401


@pytest.mark.parametrize(("offset", "status"), [(-1, 200), (1, 200), (-3, 401), (3, 401)])
async def test_totp_accepts_only_small_clock_drift(
    api: httpx.AsyncClient,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
    offset: int,
    status: int,
) -> None:
    user, uri, _ = staff
    await log_in(api, user.email)

    response = await api.post("/auth/2fa/verify", json={"code": totp_code(uri, clock, offset)})

    assert response.status_code == status


async def test_recovery_code_works_once(
    new_client: ClientFactory,
    staff: tuple[User, str, list[str]],
    db_session: AsyncSession,
) -> None:
    user, _, codes = staff
    first = await new_client()
    second = await new_client()
    await log_in(first, user.email)
    await log_in(second, user.email)
    body = {"recovery_code": codes[0]}

    assert (await first.post("/auth/2fa/verify", json=body)).status_code == 200
    assert (await second.post("/auth/2fa/verify", json=body)).status_code == 401
    actions = await db_session.scalars(select(AuditLog.action))
    assert "auth.recovery_code_used" in actions.all()


async def test_repeated_wrong_codes_lock_the_account(
    new_client: ClientFactory,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
    api_app: FastAPI,
) -> None:
    user, uri, _ = staff
    client = await new_client()
    await log_in(client, user.email)

    for _ in range(5):
        response = await client.post("/auth/2fa/verify", json={"code": "000000"})
        assert response.status_code == 401
        api_app.state.rate_limit_backend = InMemoryRateLimitBackend()

    response = await client.post("/auth/2fa/verify", json={"code": totp_code(uri, clock)})

    assert response.status_code == 401


async def test_two_factor_verification_is_rate_limited(
    api: httpx.AsyncClient, staff: tuple[User, str, list[str]]
) -> None:
    await log_in(api, staff[0].email)

    responses = [await api.post("/auth/2fa/verify", json={"code": "000000"}) for _ in range(6)]

    assert responses[-1].status_code == 429


async def test_two_factor_rate_limit_is_per_session(
    new_client: ClientFactory, staff: tuple[User, str, list[str]], make_user: UserFactory
) -> None:
    colleague = await make_user(UserRole.PSYCHOLOGIST)
    first = await new_client()
    second = await new_client()
    await log_in(first, staff[0].email)
    await log_in(second, colleague.email)

    for _ in range(5):
        await first.post("/auth/2fa/verify", json={"code": "000000"})

    response = await second.post("/auth/2fa/setup")
    assert response.status_code == 200
    enable = await second.post("/auth/2fa/enable", json={"code": "000000"})
    assert enable.status_code == 401


async def test_disable_requires_password_and_code(
    api: httpx.AsyncClient,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
    db_session: AsyncSession,
) -> None:
    user, uri, _ = staff
    await log_in(api, user.email)
    await api.post("/auth/2fa/verify", json={"code": totp_code(uri, clock)})
    clock.advance(seconds=90)

    wrong = await api.post(
        "/auth/2fa/disable", json={"password": "wrong-password", "code": totp_code(uri, clock)}
    )
    assert wrong.status_code == 401

    clock.advance(seconds=90)
    response = await api.post(
        "/auth/2fa/disable", json={"password": DEFAULT_PASSWORD, "code": totp_code(uri, clock)}
    )

    assert response.status_code == 204
    await db_session.refresh(user)
    assert user.totp_enabled is False
    assert user.totp_secret_encrypted is None
    assert (await db_session.scalars(select(AuthRecoveryCode))).all() == []


async def test_disable_ends_other_sessions(
    new_client: ClientFactory,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
) -> None:
    user, uri, _ = staff
    current = await new_client()
    other = await new_client()
    await log_in(current, user.email)
    await log_in(other, user.email)
    await current.post("/auth/2fa/verify", json={"code": totp_code(uri, clock)})
    clock.advance(seconds=90)

    await current.post(
        "/auth/2fa/disable", json={"password": DEFAULT_PASSWORD, "code": totp_code(uri, clock)}
    )

    assert (await other.get("/auth/me")).status_code == 401
    assert (await current.get("/auth/me")).status_code == 200


async def test_setup_is_rejected_when_already_enabled(
    api: httpx.AsyncClient, staff: tuple[User, str, list[str]], clock: FakeClock
) -> None:
    user, uri, _ = staff
    await log_in(api, user.email)
    await api.post("/auth/2fa/verify", json={"code": totp_code(uri, clock)})

    response = await api.post("/auth/2fa/setup")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TWO_FACTOR_STATE_INVALID"


async def test_two_factor_changes_are_audited(
    staff: tuple[User, str, list[str]], db_session: AsyncSession
) -> None:
    actions = (await db_session.scalars(select(AuditLog.action))).all()

    assert "auth.2fa_enabled" in actions


class TestRequireStaff2fa:
    @pytest.fixture
    def api_settings(self) -> dict[str, Any]:
        return {"require_staff_2fa": True}

    @staticmethod
    def add_staff_route(app: FastAPI) -> None:
        @app.get("/panel", dependencies=[Depends(require_roles(UserRole.PSYCHOLOGIST))])
        async def panel() -> dict[str, bool]:
            return {"ok": True}

    async def test_staff_are_blocked_until_2fa_is_enabled(
        self,
        api: httpx.AsyncClient,
        api_app: FastAPI,
        make_user: UserFactory,
        clock: FakeClock,
    ) -> None:
        self.add_staff_route(api_app)
        await log_in(api, (await make_user(UserRole.PSYCHOLOGIST)).email)

        blocked = await api.get("http://test/panel")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "TWO_FACTOR_SETUP_REQUIRED"
        assert (await api.get("/auth/me")).json()["two_factor_setup_required"] is True

        await enable_two_factor(api, clock)

        assert (await api.get("http://test/panel")).status_code == 200

    async def test_clients_are_not_affected(
        self, api: httpx.AsyncClient, make_user: UserFactory
    ) -> None:
        await log_in(api, (await make_user(UserRole.CLIENT)).email)

        assert (await api.get("/auth/sessions")).status_code == 200


async def test_session_row_records_mfa_state(
    api: httpx.AsyncClient,
    staff: tuple[User, str, list[str]],
    clock: FakeClock,
    db_session: AsyncSession,
) -> None:
    user, uri, _ = staff
    await log_in(api, user.email)
    await api.post("/auth/2fa/verify", json={"code": totp_code(uri, clock)})

    rows = (await db_session.scalars(select(AuthSession.mfa_verified))).all()

    assert all(rows)
