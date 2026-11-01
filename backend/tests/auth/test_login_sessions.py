from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment
from app.core.security import hash_token
from app.modules.audit.models import AuditLog
from app.modules.auth.csrf import CSRF_COOKIE, SESSION_COOKIE
from app.modules.auth.models import AuthSession
from app.modules.notifications.service import EmailTemplate
from app.modules.users.models import UserRole
from tests.conftest import (
    CSRF_HEADER,
    DEFAULT_PASSWORD,
    ClientFactory,
    FakeClock,
    FakeMailer,
    UserFactory,
    log_in,
    replace_cookie,
)


async def audit_actions(session: AsyncSession) -> list[str]:
    session.expire_all()
    rows = await session.scalars(select(AuditLog).order_by(AuditLog.occurred_at))
    return [row.action for row in rows]


async def test_login_sets_secure_session_cookie_and_stores_only_a_hash(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user()

    response = await log_in(api, user.email)

    assert response.status_code == 200
    assert response.json()["mfa_required"] is False
    session_cookie = next(
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(SESSION_COOKIE)
    )
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    token = api.cookies[SESSION_COOKIE]
    stored = (await db_session.scalars(select(AuthSession))).all()
    assert [row.token_hash for row in stored] == [hash_token(token)]
    assert token not in stored[0].token_hash


async def test_session_cookies_are_marked_secure_outside_dev(make_settings: Any) -> None:
    settings = make_settings(
        environment=Environment.TEST,
        database_url="postgresql+asyncpg://app:strong@localhost/seans",
        migration_database_url="postgresql+asyncpg://owner:strong@localhost/seans",
    )

    assert settings.cookies_secure is True


async def test_me_returns_the_current_user(api: httpx.AsyncClient, make_user: UserFactory) -> None:
    user = await make_user()
    await log_in(api, user.email)

    response = await api.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["role"] == "client"


async def test_me_requires_a_session(api: httpx.AsyncClient) -> None:
    response = await api.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_wrong_password_and_unknown_email_look_the_same(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user()

    wrong_password = await log_in(api, user.email, "wrong-password-here")
    unknown_email = await log_in(api, "nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert await audit_actions(db_session) == ["auth.login_failed", "auth.login_failed"]


async def test_failed_attempts_are_stored_despite_the_error(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user()

    await log_in(api, user.email, "wrong-password-here")

    await db_session.refresh(user)
    assert user.failed_login_attempts == 1


async def test_account_locks_after_repeated_failures_and_unlocks_later(
    new_client: ClientFactory, make_user: UserFactory, clock: FakeClock
) -> None:
    user = await make_user()
    client = await new_client()
    for _ in range(5):
        await log_in(client, user.email, "wrong-password-here")

    locked = await log_in(client, user.email)
    assert locked.status_code == 401
    assert locked.json()["error"]["code"] == "INVALID_CREDENTIALS"

    clock.advance(minutes=16)
    assert (await log_in(await new_client(), user.email)).status_code == 200


async def test_successful_login_resets_the_failure_counter(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user()
    await log_in(api, user.email, "wrong-password-here")

    await log_in(api, user.email)

    await db_session.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.last_login_at is not None


async def test_login_is_rate_limited(api: httpx.AsyncClient) -> None:
    responses = [await log_in(api, f"u{i}@example.com") for i in range(11)]

    assert responses[-1].status_code == 429


async def test_inactive_user_cannot_log_in(api: httpx.AsyncClient, make_user: UserFactory) -> None:
    user = await make_user(is_active=False)

    response = await log_in(api, user.email)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_logout_revokes_the_session(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user()
    await log_in(api, user.email)
    token = api.cookies[SESSION_COOKIE]

    assert (await api.post("/auth/logout")).status_code == 204

    row = await db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    assert row is not None
    assert row.revoked_at is not None
    replace_cookie(api, SESSION_COOKIE, token)
    assert (await api.get("/auth/me")).status_code == 401


async def test_session_expires_after_idle_timeout(
    api: httpx.AsyncClient, make_user: UserFactory, clock: FakeClock
) -> None:
    user = await make_user()
    await log_in(api, user.email)

    clock.advance(minutes=59)
    assert (await api.get("/auth/me")).status_code == 200
    clock.advance(minutes=59)
    assert (await api.get("/auth/me")).status_code == 200
    clock.advance(minutes=61)
    assert (await api.get("/auth/me")).status_code == 401


async def test_session_expires_after_absolute_lifetime(
    api: httpx.AsyncClient, make_user: UserFactory, clock: FakeClock
) -> None:
    user = await make_user()
    await log_in(api, user.email)

    for _ in range(24):
        clock.advance(minutes=59)
        await api.get("/auth/me")
    clock.advance(minutes=30)

    assert (await api.get("/auth/me")).status_code == 401


async def test_user_lists_and_revokes_own_sessions(
    new_client: ClientFactory, make_user: UserFactory
) -> None:
    user = await make_user()
    laptop = await new_client()
    phone = await new_client()
    await log_in(laptop, user.email)
    await log_in(phone, user.email)

    sessions = (await laptop.get("/auth/sessions")).json()
    assert len(sessions) == 2
    assert sum(session["is_current"] for session in sessions) == 1
    other = next(session for session in sessions if not session["is_current"])

    assert (await laptop.delete(f"/auth/sessions/{other['id']}")).status_code == 204
    assert (await phone.get("/auth/me")).status_code == 401
    assert (await laptop.get("/auth/me")).status_code == 200


async def test_user_cannot_revoke_another_users_session(
    new_client: ClientFactory, make_user: UserFactory
) -> None:
    victim = await new_client()
    attacker = await new_client()
    await log_in(victim, (await make_user()).email)
    await log_in(attacker, (await make_user()).email)
    victim_session = (await victim.get("/auth/sessions")).json()[0]["id"]

    response = await attacker.delete(f"/auth/sessions/{victim_session}")

    assert response.status_code == 404
    assert (await victim.get("/auth/me")).status_code == 200


async def test_revoking_the_current_session_clears_its_cookie(
    api: httpx.AsyncClient, make_user: UserFactory
) -> None:
    await log_in(api, (await make_user()).email)
    session_id = (await api.get("/auth/sessions")).json()[0]["id"]

    assert (await api.delete(f"/auth/sessions/{session_id}")).status_code == 204

    assert (await api.get("/auth/me")).status_code == 401


class TestCsrf:
    async def test_post_without_token_is_rejected(self, api: httpx.AsyncClient) -> None:
        api.cookies.delete(CSRF_COOKIE)

        response = await api.post(
            "/auth/login", json={"email": "a@example.com", "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_INVALID"

    async def test_header_must_match_cookie(self, api: httpx.AsyncClient) -> None:
        response = await api.post("/auth/logout", headers={CSRF_HEADER: "forged.value"})

        assert response.status_code == 403

    async def test_matching_but_unsigned_pair_is_rejected(self, api: httpx.AsyncClient) -> None:
        replace_cookie(api, CSRF_COOKIE, "attacker.chosen")

        response = await api.post(
            "/auth/login", json={"email": "a@example.com", "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 403

    async def test_anonymous_token_is_rejected_once_logged_in(
        self, api: httpx.AsyncClient, make_user: UserFactory
    ) -> None:
        anonymous_token = api.cookies[CSRF_COOKIE]
        await log_in(api, (await make_user()).email)
        replace_cookie(api, CSRF_COOKIE, anonymous_token)

        response = await api.post("/auth/logout")

        assert response.status_code == 403

    async def test_token_of_another_session_is_rejected(
        self, new_client: ClientFactory, make_user: UserFactory
    ) -> None:
        first = await new_client()
        second = await new_client()
        await log_in(first, (await make_user()).email)
        await log_in(second, (await make_user()).email)
        token = first.cookies[CSRF_COOKIE]
        replace_cookie(second, CSRF_COOKIE, token)

        assert (await second.post("/auth/logout")).status_code == 403

    async def test_login_returns_a_token_bound_to_the_new_session(
        self, api: httpx.AsyncClient, make_user: UserFactory
    ) -> None:
        response = await log_in(api, (await make_user()).email)

        assert response.json()["csrf_token"] == api.cookies[CSRF_COOKIE]
        assert (await api.post("/auth/logout")).status_code == 204


class TestPasswordReset:
    async def test_request_responds_identically_for_unknown_email(
        self, api: httpx.AsyncClient, make_user: UserFactory, mailer: FakeMailer
    ) -> None:
        user = await make_user()

        known = await api.post("/auth/password-reset/request", json={"email": user.email})
        unknown = await api.post(
            "/auth/password-reset/request", json={"email": "nobody@example.com"}
        )

        assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
        assert [email.to for email in mailer.sent] == [user.email]
        assert mailer.sent[0].template is EmailTemplate.PASSWORD_RESET

    async def test_full_flow_changes_password_and_ends_sessions(
        self,
        new_client: ClientFactory,
        make_user: UserFactory,
        mailer: FakeMailer,
        db_session: AsyncSession,
    ) -> None:
        user = await make_user()
        old_device = await new_client()
        await log_in(old_device, user.email)
        client = await new_client()
        new_password = "another-long-test-passphrase"

        await client.post("/auth/password-reset/request", json={"email": user.email})
        token = mailer.last_token(user.email)
        response = await client.post(
            "/auth/password-reset/confirm", json={"token": token, "new_password": new_password}
        )

        assert response.status_code == 204
        assert (await old_device.get("/auth/me")).status_code == 401
        assert (await log_in(client, user.email)).status_code == 401
        assert (await log_in(await new_client(), user.email, new_password)).status_code == 200
        assert "auth.password_reset_completed" in await audit_actions(db_session)

    async def test_token_works_only_once(
        self, api: httpx.AsyncClient, make_user: UserFactory, mailer: FakeMailer
    ) -> None:
        user = await make_user()
        await api.post("/auth/password-reset/request", json={"email": user.email})
        body = {"token": mailer.last_token(user.email), "new_password": "another-long-passphrase"}

        assert (await api.post("/auth/password-reset/confirm", json=body)).status_code == 204
        assert (await api.post("/auth/password-reset/confirm", json=body)).status_code == 400

    async def test_expired_token_is_rejected(
        self, api: httpx.AsyncClient, make_user: UserFactory, mailer: FakeMailer, clock: FakeClock
    ) -> None:
        user = await make_user()
        await api.post("/auth/password-reset/request", json={"email": user.email})
        clock.advance(minutes=31)

        response = await api.post(
            "/auth/password-reset/confirm",
            json={
                "token": mailer.last_token(user.email),
                "new_password": "another-long-passphrase",
            },
        )

        assert response.status_code == 400

    async def test_new_request_invalidates_the_previous_token(
        self, api: httpx.AsyncClient, make_user: UserFactory, mailer: FakeMailer
    ) -> None:
        user = await make_user()
        await api.post("/auth/password-reset/request", json={"email": user.email})
        first = mailer.last_token(user.email)
        await api.post("/auth/password-reset/request", json={"email": user.email})

        response = await api.post(
            "/auth/password-reset/confirm",
            json={"token": first, "new_password": "another-long-passphrase"},
        )

        assert response.status_code == 400

    async def test_weak_new_password_keeps_the_token_usable(
        self, api: httpx.AsyncClient, make_user: UserFactory, mailer: FakeMailer
    ) -> None:
        user = await make_user()
        await api.post("/auth/password-reset/request", json={"email": user.email})
        token = mailer.last_token(user.email)

        weak = await api.post(
            "/auth/password-reset/confirm", json={"token": token, "new_password": "1234567890"}
        )
        strong = await api.post(
            "/auth/password-reset/confirm",
            json={"token": token, "new_password": "another-long-passphrase"},
        )

        assert weak.status_code == 422
        assert strong.status_code == 204

    async def test_reset_clears_a_lockout(
        self, new_client: ClientFactory, make_user: UserFactory, mailer: FakeMailer
    ) -> None:
        user = await make_user()
        client = await new_client()
        for _ in range(5):
            await log_in(client, user.email, "wrong-password-here")
        new_password = "another-long-test-passphrase"

        await client.post("/auth/password-reset/request", json={"email": user.email})
        await client.post(
            "/auth/password-reset/confirm",
            json={"token": mailer.last_token(user.email), "new_password": new_password},
        )

        assert (await log_in(await new_client(), user.email, new_password)).status_code == 200


async def test_login_audit_trail(
    api: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    await log_in(api, (await make_user(UserRole.CLIENT)).email)
    await api.post("/auth/logout")

    assert await audit_actions(db_session) == ["auth.login_succeeded", "auth.logout"]


@pytest.mark.parametrize("email", ["not-an-email", ""])
async def test_login_validates_input(api: httpx.AsyncClient, email: str) -> None:
    response = await log_in(api, email)

    assert response.status_code == 422
