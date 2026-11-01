import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.auth.dependencies import (
    api_routes,
    assert_routes_declare_access,
    route_policies,
)
from app.modules.auth.passwords import WeakPasswordError
from app.modules.notifications.service import EmailTemplate
from app.modules.users.actor import Actor, ForbiddenError
from app.modules.users.admin_service import AdminService, create_first_admin
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.modules.users.repository import UserRepository
from app.modules.users.service import EmailTakenError, UserService
from tests.auth.test_two_factor import enable_two_factor
from tests.conftest import (
    DEFAULT_PASSWORD,
    ClientFactory,
    FakeClock,
    FakeMailer,
    SettingsFactory,
    UserFactory,
    log_in,
)

PSYCHOLOGIST = {
    "email": "dr.invited@example.com",
    "first_name": "Grace",
    "last_name": "Hopper",
    "slug": "grace-hopper",
}


@pytest.fixture
async def admin(api: httpx.AsyncClient, make_user: UserFactory) -> User:
    user = await make_user(UserRole.ADMIN)
    await log_in(api, user.email)
    return user


async def test_admin_creates_psychologist_who_sets_a_password_from_the_invitation(
    api: httpx.AsyncClient,
    admin: User,
    new_client: ClientFactory,
    mailer: FakeMailer,
    db_session: AsyncSession,
) -> None:
    response = await api.post("/admin/psychologists", json=PSYCHOLOGIST)

    assert response.status_code == 201
    assert response.json()["role"] == "psychologist"
    assert mailer.sent[-1].template is EmailTemplate.INVITATION
    profile = await db_session.scalar(
        select(PsychologistProfile).where(PsychologistProfile.slug == "grace-hopper")
    )
    assert profile is not None

    client = await new_client()
    assert (await log_in(client, PSYCHOLOGIST["email"])).status_code == 401

    confirmed = await client.post(
        "/auth/password-reset/confirm",
        json={"token": mailer.last_token(PSYCHOLOGIST["email"]), "new_password": DEFAULT_PASSWORD},
    )
    assert confirmed.status_code == 204
    login = await log_in(client, PSYCHOLOGIST["email"])
    assert login.status_code == 200
    assert (await client.get("/auth/me")).json()["email_verified"] is True


@pytest.mark.parametrize(
    ("change", "code"),
    [({"email": "taken@example.com"}, "EMAIL_TAKEN"), ({"slug": "taken-slug"}, "SLUG_TAKEN")],
)
async def test_duplicate_email_or_slug_is_rejected(
    api: httpx.AsyncClient, admin: User, change: dict[str, str], code: str
) -> None:
    taken = PSYCHOLOGIST | {"email": "taken@example.com", "slug": "taken-slug"}
    assert (await api.post("/admin/psychologists", json=taken)).status_code == 201

    response = await api.post("/admin/psychologists", json=PSYCHOLOGIST | change)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("role", [UserRole.PSYCHOLOGIST, UserRole.CLIENT])
async def test_non_admins_cannot_use_admin_endpoints(
    api: httpx.AsyncClient, make_user: UserFactory, role: UserRole
) -> None:
    target = await make_user()
    await log_in(api, (await make_user(role)).email)

    responses = [
        await api.post("/admin/psychologists", json=PSYCHOLOGIST),
        await api.post(f"/admin/users/{target.id}/deactivate"),
        await api.post(f"/admin/users/{target.id}/activate"),
        await api.post(f"/admin/users/{target.id}/2fa/reset"),
    ]

    assert {response.status_code for response in responses} == {403}


async def test_anonymous_cannot_use_admin_endpoints(api: httpx.AsyncClient) -> None:
    response = await api.post("/admin/psychologists", json=PSYCHOLOGIST)

    assert response.status_code == 401


async def test_deactivation_ends_sessions_and_activation_restores_login(
    api: httpx.AsyncClient,
    admin: User,
    new_client: ClientFactory,
    make_user: UserFactory,
) -> None:
    target = await make_user()
    client = await new_client()
    await log_in(client, target.email)

    response = await api.post(f"/admin/users/{target.id}/deactivate")

    assert response.json()["is_active"] is False
    assert (await client.get("/auth/me")).status_code == 401
    assert (await log_in(await new_client(), target.email)).status_code == 401

    await api.post(f"/admin/users/{target.id}/activate")
    assert (await log_in(await new_client(), target.email)).status_code == 200


async def test_admin_cannot_deactivate_themselves(api: httpx.AsyncClient, admin: User) -> None:
    response = await api.post(f"/admin/users/{admin.id}/deactivate")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANNOT_TARGET_SELF"


async def test_unknown_user_returns_404(api: httpx.AsyncClient, admin: User) -> None:
    response = await api.post("/admin/users/00000000-0000-0000-0000-000000000000/activate")

    assert response.status_code == 404


async def test_admin_resets_lost_two_factor(
    api: httpx.AsyncClient,
    admin: User,
    new_client: ClientFactory,
    make_user: UserFactory,
    clock: FakeClock,
    db_session: AsyncSession,
) -> None:
    target = await make_user(UserRole.PSYCHOLOGIST)
    client = await new_client()
    await log_in(client, target.email)
    await enable_two_factor(client, clock)

    response = await api.post(f"/admin/users/{target.id}/2fa/reset")

    assert response.status_code == 204
    await db_session.refresh(target)
    assert target.totp_enabled is False
    assert (await client.get("/auth/me")).status_code == 401
    assert "auth.2fa_reset" in (await db_session.scalars(select(AuditLog.action))).all()


async def test_admin_service_enforces_the_role_without_a_router(
    db_session: AsyncSession, make_user: UserFactory, clock: FakeClock
) -> None:
    client_actor = Actor(user_id=(await make_user()).id, role=UserRole.CLIENT)
    target = await make_user()
    service = AdminService(
        UserService(UserRepository(db_session), clock),
        None,  # type: ignore[arg-type]
        AuditService(AuditRepository(db_session)),
        clock,
    )

    with pytest.raises(ForbiddenError):
        await service.set_active(client_actor, target.id, active=False, ip_address=None)


class TestCreateFirstAdmin:
    async def test_creates_a_verified_admin(self, db_session: AsyncSession) -> None:
        user = await create_first_admin(
            UserService(UserRepository(db_session), utcnow),
            AuditService(AuditRepository(db_session)),
            utcnow,
            "root@example.com",
            DEFAULT_PASSWORD,
        )

        assert user.role is UserRole.ADMIN
        assert user.email_verified_at is not None

    async def test_rejects_weak_passwords(self, db_session: AsyncSession) -> None:
        with pytest.raises(WeakPasswordError):
            await create_first_admin(
                UserService(UserRepository(db_session), utcnow),
                AuditService(AuditRepository(db_session)),
                utcnow,
                "root@example.com",
                "1234567890",
            )

    async def test_rejects_a_taken_email(
        self, db_session: AsyncSession, make_user: UserFactory
    ) -> None:
        user = await make_user()

        with pytest.raises(EmailTakenError):
            await create_first_admin(
                UserService(UserRepository(db_session), utcnow),
                AuditService(AuditRepository(db_session)),
                utcnow,
                user.email,
                DEFAULT_PASSWORD,
            )


async def test_every_api_route_declares_an_access_policy(api_app: FastAPI) -> None:
    routes = api_routes(api_app)

    assert "/api/v1/auth/login" in {path for path, _ in routes}
    assert "/api/v1/admin/users/{user_id}/deactivate" in {path for path, _ in routes}
    assert all(route_policies(route.dependant) for _, route in routes)


async def test_public_routes_are_the_expected_ones(api_app: FastAPI) -> None:
    public = {
        path for path, route in api_routes(api_app) if "public" in route_policies(route.dependant)
    }

    assert public == {
        "/api/v1/health",
        "/api/v1/auth/csrf",
        "/api/v1/auth/register",
        "/api/v1/auth/verify-email",
        "/api/v1/auth/login",
        "/api/v1/auth/password-reset/request",
        "/api/v1/auth/password-reset/confirm",
    }


async def test_startup_fails_for_a_route_without_a_policy(
    make_settings: SettingsFactory,
) -> None:
    app = create_app(make_settings())

    @app.get("/api/v1/forgotten")
    async def forgotten() -> None:
        return None

    with pytest.raises(RuntimeError, match="/api/v1/forgotten"):
        assert_routes_declare_access(app)
    await app.state.database.dispose()
