import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.clients.models import Client
from app.modules.consent.models import Consent, ConsentType
from app.modules.notifications.service import EmailTemplate
from app.modules.users.models import User, UserRole
from tests.conftest import DEFAULT_PASSWORD, ClientFactory, FakeClock, FakeMailer, log_in

EMAIL = "new.client@example.com"


def registration(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "email": EMAIL,
        "password": DEFAULT_PASSWORD,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "phone": "+90 532 123 45 67",
        "kvkk_accepted": True,
    }
    return values | overrides


async def test_register_creates_user_client_consent_and_sends_email(
    api: httpx.AsyncClient, db_session: AsyncSession, mailer: FakeMailer
) -> None:
    response = await api.post("/auth/register", json=registration())

    assert response.status_code == 202
    user = await db_session.scalar(select(User).where(User.email == EMAIL))
    assert user is not None
    assert user.role is UserRole.CLIENT
    assert user.email_verified_at is None
    assert user.password_hash is not None
    assert DEFAULT_PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")

    client = await db_session.scalar(select(Client).where(Client.user_id == user.id))
    assert client is not None
    assert client.created_by_user_id == user.id

    consent = await db_session.scalar(select(Consent).where(Consent.client_id == client.id))
    assert consent is not None
    assert consent.consent_type is ConsentType.KVKK_DISCLOSURE
    assert consent.version == "1.0"
    assert str(consent.ip_address) == "127.0.0.1"

    assert [email.template for email in mailer.sent] == [EmailTemplate.VERIFY_EMAIL]


async def test_register_requires_kvkk_acceptance(api: httpx.AsyncClient) -> None:
    response = await api.post("/auth/register", json=registration(kvkk_accepted=False))

    assert response.status_code == 422


async def test_register_cannot_choose_a_role(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await api.post("/auth/register", json=registration(role="admin"))

    user = await db_session.scalar(select(User).where(User.email == EMAIL))
    assert user is not None
    assert user.role is UserRole.CLIENT


async def test_duplicate_registration_responds_identically(
    api: httpx.AsyncClient, db_session: AsyncSession, mailer: FakeMailer
) -> None:
    first = await api.post("/auth/register", json=registration())
    second = await api.post("/auth/register", json=registration(email=EMAIL.upper()))

    assert (first.status_code, first.json()) == (second.status_code, second.json())
    assert mailer.sent[-1].template is EmailTemplate.ACCOUNT_EXISTS
    assert len((await db_session.scalars(select(User).where(User.email == EMAIL))).all()) == 1


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("short", "too_short"),
        ("1234567890", "too_common"),
        ("QWERTYUIOP", "too_common"),
        (EMAIL, "too_common"),
    ],
)
async def test_weak_passwords_are_rejected(
    api: httpx.AsyncClient, password: str, reason: str
) -> None:
    response = await api.post("/auth/register", json=registration(password=password))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WEAK_PASSWORD"
    assert response.json()["error"]["details"] == {"reason": reason}


async def test_email_verification_flow(
    api: httpx.AsyncClient, db_session: AsyncSession, mailer: FakeMailer
) -> None:
    await api.post("/auth/register", json=registration())
    token = mailer.last_token(EMAIL)

    response = await api.post("/auth/verify-email", json={"token": token})

    assert response.status_code == 204
    user = await db_session.scalar(
        select(User).where(User.email == EMAIL).execution_options(populate_existing=True)
    )
    assert user is not None
    assert user.email_verified_at is not None
    assert (await api.post("/auth/verify-email", json={"token": token})).status_code == 400


async def test_expired_verification_token_is_rejected(
    api: httpx.AsyncClient, mailer: FakeMailer, clock: FakeClock
) -> None:
    await api.post("/auth/register", json=registration())
    clock.advance(hours=25)

    response = await api.post("/auth/verify-email", json={"token": mailer.last_token(EMAIL)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


async def test_unknown_verification_token_is_rejected(api: httpx.AsyncClient) -> None:
    response = await api.post("/auth/verify-email", json={"token": "unknown"})

    assert response.status_code == 400


async def test_unverified_user_can_log_in_and_resend_verification(
    api: httpx.AsyncClient, mailer: FakeMailer
) -> None:
    await api.post("/auth/register", json=registration())
    first_token = mailer.last_token(EMAIL)

    assert (await log_in(api, EMAIL)).status_code == 200
    me = await api.get("/auth/me")
    assert me.json()["email_verified"] is False

    assert (await api.post("/auth/verify-email/resend")).status_code == 202
    assert mailer.last_token(EMAIL) != first_token
    assert (await api.post("/auth/verify-email", json={"token": first_token})).status_code == 400
    response = await api.post("/auth/verify-email", json={"token": mailer.last_token(EMAIL)})
    assert response.status_code == 204


async def test_registration_is_rate_limited(new_client: ClientFactory) -> None:
    client = await new_client()

    responses = [
        await client.post("/auth/register", json=registration(email=f"u{i}@example.com"))
        for i in range(11)
    ]

    assert responses[-1].status_code == 429


async def test_audit_entry_does_not_contain_personal_data(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await api.post("/auth/register", json=registration())

    entries = (await db_session.scalars(select(AuditLog))).all()

    assert [entry.action for entry in entries] == ["users.registered"]
    assert entries[0].event_metadata == {}
