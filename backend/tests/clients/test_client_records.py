from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.clients.models import Client, PsychologistClient
from app.modules.consent.models import Consent, ConsentType
from app.modules.users.models import User, UserRole
from tests.clients.conftest import load_client, load_relationship
from tests.conftest import (
    UNKNOWN_ID,
    ClientRecordFactory,
    FakeClock,
    UserFactory,
    audit_entries,
)

CLIENT: dict[str, Any] = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "phone": "+90 532 123 45 67",
    "email": "ada@example.com",
    "birth_date": "1990-05-17",
    "kvkk_disclosure_given": True,
}
CHANGES: dict[str, Any] = {
    "first_name": "Grace",
    "last_name": "Hopper",
    "email": "grace@example.com",
    "phone": "+90 555 765 43 21",
    "birth_date": "1985-12-09",
}
INVALID_FIELDS: list[dict[str, Any]] = [
    {"first_name": ""},
    {"first_name": "   "},
    {"first_name": "a" * 101},
    {"first_name": "a\x00b"},
    {"last_name": ""},
    {"last_name": "a" * 101},
    {"phone": "not a phone"},
    {"phone": "123"},
    {"phone": "1" * 30},
    {"email": "not an email"},
    {"email": ""},
    {"email": f"{'a' * 250}@example.com"},
    {"birth_date": "not a date"},
    {"birth_date": "1990-13-01"},
]


async def clients_named(session: AsyncSession, last_name: str) -> list[Client]:
    result = await session.scalars(select(Client).where(Client.last_name == last_name))
    return list(result.all())


class TestCreate:
    async def test_psychologist_creates_a_client_in_their_care(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        db_session: AsyncSession,
    ) -> None:
        response = await api.post("/staff/clients", json=CLIENT)

        assert response.status_code == 201
        body = response.json()
        assert body == {
            "id": body["id"],
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "+90 532 123 45 67",
            "birth_date": "1990-05-17",
            "has_account": False,
            "created_at": body["created_at"],
        }

        [client] = await clients_named(db_session, "Lovelace")
        assert str(client.id) == body["id"]
        assert client.user_id is None
        assert client.created_by_user_id == psychologist.id
        assert client.deleted_at is None
        relationship = await load_relationship(db_session, psychologist, client)
        assert relationship.ended_at is None

    async def test_new_client_appears_in_the_list(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        created = await api.post("/staff/clients", json=CLIENT)

        listed = await api.get("/staff/clients")

        assert listed.json()["items"] == [created.json()]

    async def test_email_and_birth_date_are_optional(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        required = {
            key: value for key, value in CLIENT.items() if key not in {"email", "birth_date"}
        }

        response = await api.post("/staff/clients", json=required)

        assert response.status_code == 201
        assert response.json()["email"] is None
        assert response.json()["birth_date"] is None

    async def test_kvkk_disclosure_is_recorded_by_the_psychologist(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        db_session: AsyncSession,
    ) -> None:
        response = await api.post("/staff/clients", json=CLIENT)

        consent = await db_session.scalar(
            select(Consent).where(Consent.client_id == response.json()["id"])
        )
        assert consent is not None
        assert consent.consent_type is ConsentType.KVKK_DISCLOSURE
        assert consent.version == "1.0"
        assert consent.recorded_by_user_id == psychologist.id
        assert consent.ip_address is None
        assert consent.revoked_at is None

    @pytest.mark.parametrize("api_settings", [{"kvkk_disclosure_version": "2.5"}])
    async def test_kvkk_version_comes_from_the_settings(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        response = await api.post("/staff/clients", json=CLIENT)

        version = await db_session.scalar(
            select(Consent.version).where(Consent.client_id == response.json()["id"])
        )
        assert version == "2.5"

    @pytest.mark.parametrize("given", [False, None, "true", "yes", 0, 1])
    async def test_refuses_a_client_without_the_disclosure(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        db_session: AsyncSession,
        given: object,
    ) -> None:
        response = await api.post("/staff/clients", json=CLIENT | {"kvkk_disclosure_given": given})

        assert response.status_code == 422
        assert await clients_named(db_session, "Lovelace") == []

    async def test_refuses_a_body_without_the_disclosure_field(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        body = {key: value for key, value in CLIENT.items() if key != "kvkk_disclosure_given"}

        response = await api.post("/staff/clients", json=body)

        assert response.status_code == 422

    @pytest.mark.parametrize("missing", ["first_name", "last_name", "phone"])
    async def test_requires_the_essential_fields(
        self, api: httpx.AsyncClient, psychologist: User, missing: str
    ) -> None:
        body = {key: value for key, value in CLIENT.items() if key != missing}

        response = await api.post("/staff/clients", json=body)

        assert response.status_code == 422

    @pytest.mark.parametrize("change", INVALID_FIELDS)
    async def test_rejects_invalid_values(
        self, api: httpx.AsyncClient, psychologist: User, change: dict[str, Any]
    ) -> None:
        response = await api.post("/staff/clients", json=CLIENT | change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_same_email_is_allowed_on_several_records(
        self,
        api: httpx.AsyncClient,
        colleague_api: httpx.AsyncClient,
        psychologist: User,
        db_session: AsyncSession,
    ) -> None:
        first = await api.post("/staff/clients", json=CLIENT)
        again = await api.post("/staff/clients", json=CLIENT)
        by_colleague = await colleague_api.post("/staff/clients", json=CLIENT)

        assert {first.status_code, again.status_code, by_colleague.status_code} == {201}
        assert len({first.json()["id"], again.json()["id"], by_colleague.json()["id"]}) == 3
        assert len(await clients_named(db_session, "Lovelace")) == 3

    async def test_nothing_reveals_that_an_email_belongs_to_an_account(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_user: UserFactory,
        db_session: AsyncSession,
    ) -> None:
        account = await make_user(UserRole.CLIENT, email="taken@example.com")

        response = await api.post("/staff/clients", json=CLIENT | {"email": account.email})

        assert response.status_code == 201
        assert response.json()["has_account"] is False
        [client] = await clients_named(db_session, "Lovelace")
        assert client.user_id is None

    async def test_ignores_fields_that_are_not_part_of_the_form(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_user: UserFactory,
        db_session: AsyncSession,
    ) -> None:
        other = await make_user()

        response = await api.post(
            "/staff/clients",
            json=CLIENT
            | {
                "id": str(UNKNOWN_ID),
                "user_id": str(other.id),
                "created_by_user_id": str(other.id),
                "deleted_at": "2000-01-01T00:00:00Z",
            },
        )

        assert response.status_code == 201
        [client] = await clients_named(db_session, "Lovelace")
        assert client.id != UNKNOWN_ID
        assert client.user_id is None
        assert client.created_by_user_id == psychologist.id
        assert client.deleted_at is None

    async def test_is_audited_without_personal_data(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        response = await api.post("/staff/clients", json=CLIENT)

        [entry] = await audit_entries(db_session, AuditAction.CLIENT_CREATED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "client"
        assert str(entry.entity_id) == response.json()["id"]
        assert str(entry.ip_address) == "127.0.0.1"
        assert entry.event_metadata == {}


class TestRead:
    async def test_psychologist_reads_a_client_in_their_care(
        self, api: httpx.AsyncClient, psychologist: User, make_client_record: ClientRecordFactory
    ) -> None:
        client = await make_client_record(
            psychologist,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="+90 532 123 45 67",
            birth_date=date(1990, 5, 17),
        )

        response = await api.get(f"/staff/clients/{client.id}")

        assert response.status_code == 200
        assert response.json() == {
            "id": str(client.id),
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "+90 532 123 45 67",
            "birth_date": "1990-05-17",
            "has_account": False,
            "created_at": response.json()["created_at"],
        }

    async def test_shows_when_the_client_has_a_login_account(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
    ) -> None:
        account = await make_user()
        client = await make_client_record(psychologist, user_id=account.id)

        response = await api.get(f"/staff/clients/{client.id}")

        assert response.json()["has_account"] is True

    async def test_client_of_a_colleague_looks_like_an_unknown_client(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        client = await make_client_record(colleague)

        forbidden = await api.get(f"/staff/clients/{client.id}")
        unknown = await api.get(f"/staff/clients/{UNKNOWN_ID}")

        assert forbidden.status_code == 404
        assert forbidden.json() == unknown.json()
        assert forbidden.json()["error"]["code"] == "NOT_FOUND"

    async def test_admin_reads_any_client(
        self,
        api: httpx.AsyncClient,
        admin: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        client = await make_client_record(colleague)

        response = await api.get(f"/staff/clients/{client.id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(client.id)

    async def test_an_invalid_id_is_rejected(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.get("/staff/clients/not-a-uuid")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_admin_cannot_read_a_deleted_client(
        self,
        api: httpx.AsyncClient,
        admin: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
        clock: FakeClock,
    ) -> None:
        client = await make_client_record(colleague, deleted_at=clock.now)

        response = await api.get(f"/staff/clients/{client.id}")

        assert response.status_code == 404


class TestUpdate:
    @pytest.fixture
    async def record(self, psychologist: User, make_client_record: ClientRecordFactory) -> Client:
        return await make_client_record(
            psychologist,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="+90 532 123 45 67",
            birth_date=date(1990, 5, 17),
        )

    async def test_psychologist_updates_every_editable_field(
        self, api: httpx.AsyncClient, record: Client, db_session: AsyncSession
    ) -> None:
        response = await api.patch(f"/staff/clients/{record.id}", json=CHANGES)

        assert response.status_code == 200
        assert {name: response.json()[name] for name in CHANGES} == CHANGES
        stored = await load_client(db_session, record)
        assert stored.first_name == "Grace"
        assert stored.last_name == "Hopper"
        assert stored.email == "grace@example.com"
        assert stored.phone == "+90 555 765 43 21"
        assert stored.birth_date == date(1985, 12, 9)

    async def test_leaves_absent_fields_alone(self, api: httpx.AsyncClient, record: Client) -> None:
        response = await api.patch(f"/staff/clients/{record.id}", json={"first_name": "Grace"})

        assert response.json()["first_name"] == "Grace"
        assert response.json()["last_name"] == "Lovelace"
        assert response.json()["email"] == "ada@example.com"
        assert response.json()["birth_date"] == "1990-05-17"

    @pytest.mark.parametrize("field", ["email", "birth_date"])
    async def test_nullable_fields_can_be_cleared(
        self, api: httpx.AsyncClient, record: Client, db_session: AsyncSession, field: str
    ) -> None:
        response = await api.patch(f"/staff/clients/{record.id}", json={field: None})

        assert response.status_code == 200
        assert response.json()[field] is None
        assert getattr(await load_client(db_session, record), field) is None

    @pytest.mark.parametrize("field", ["first_name", "last_name", "phone"])
    async def test_explicit_null_is_rejected_for_other_fields(
        self, api: httpx.AsyncClient, record: Client, field: str
    ) -> None:
        response = await api.patch(f"/staff/clients/{record.id}", json={field: None})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["fields"] == [
            {"field": f"body.{field}", "code": "null_not_allowed"}
        ]

    @pytest.mark.parametrize("change", INVALID_FIELDS)
    async def test_rejects_invalid_values(
        self, api: httpx.AsyncClient, record: Client, change: dict[str, Any]
    ) -> None:
        response = await api.patch(f"/staff/clients/{record.id}", json=change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_never_touches_the_email_of_the_login_account(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
    ) -> None:
        account = await make_user(email="login@example.com")
        client = await make_client_record(
            psychologist, user_id=account.id, email="login@example.com"
        )

        response = await api.patch(
            f"/staff/clients/{client.id}", json={"email": "changed@example.com"}
        )

        assert response.json()["email"] == "changed@example.com"
        await db_session.refresh(account)
        assert account.email == "login@example.com"

    async def test_ignores_fields_that_are_not_editable(
        self,
        api: httpx.AsyncClient,
        record: Client,
        make_user: UserFactory,
        db_session: AsyncSession,
    ) -> None:
        other = await make_user()

        response = await api.patch(
            f"/staff/clients/{record.id}",
            json={
                "id": str(UNKNOWN_ID),
                "user_id": str(other.id),
                "created_by_user_id": str(other.id),
                "deleted_at": "2000-01-01T00:00:00Z",
                "has_account": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["has_account"] is False
        stored = await load_client(db_session, record)
        assert stored.user_id is None
        assert stored.created_by_user_id != other.id
        assert stored.deleted_at is None
        assert await audit_entries(db_session, AuditAction.CLIENT_UPDATED) == []

    async def test_is_audited_with_field_names_and_no_personal_data(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        record: Client,
        db_session: AsyncSession,
    ) -> None:
        await api.patch(
            f"/staff/clients/{record.id}", json={"last_name": "Secret", "birth_date": None}
        )

        [entry] = await audit_entries(db_session, AuditAction.CLIENT_UPDATED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "client"
        assert entry.entity_id == record.id
        assert str(entry.ip_address) == "127.0.0.1"
        assert entry.event_metadata == {"changed_fields": ["birth_date", "last_name"]}

    @pytest.mark.parametrize("body", [{}, {"first_name": "Ada", "birth_date": "1990-05-17"}])
    async def test_a_patch_that_changes_nothing_is_not_audited(
        self,
        api: httpx.AsyncClient,
        record: Client,
        db_session: AsyncSession,
        body: dict[str, Any],
    ) -> None:
        response = await api.patch(f"/staff/clients/{record.id}", json=body)

        assert response.status_code == 200
        assert await audit_entries(db_session, AuditAction.CLIENT_UPDATED) == []

    async def test_a_colleagues_client_returns_404_and_stays_unchanged(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
    ) -> None:
        client = await make_client_record(colleague, first_name="Theirs")

        response = await api.patch(f"/staff/clients/{client.id}", json={"first_name": "Changed"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert (await load_client(db_session, client)).first_name == "Theirs"

    async def test_an_unknown_client_returns_404(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.patch(f"/staff/clients/{UNKNOWN_ID}", json={"first_name": "New"})

        assert response.status_code == 404


class TestBirthDate:
    TODAY = date(2000, 6, 15)

    @pytest.fixture(autouse=True)
    def fixed_time(self, clock: FakeClock) -> None:
        clock.now = datetime(2000, 6, 15, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize("birth_date", [TODAY, TODAY - timedelta(days=1)])
    async def test_today_and_earlier_dates_are_accepted(
        self, api: httpx.AsyncClient, psychologist: User, birth_date: date
    ) -> None:
        response = await api.post(
            "/staff/clients", json=CLIENT | {"birth_date": birth_date.isoformat()}
        )

        assert response.status_code == 201

    async def test_future_is_judged_by_the_injected_clock(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        tomorrow = self.TODAY + timedelta(days=1)

        response = await api.post(
            "/staff/clients", json=CLIENT | {"birth_date": tomorrow.isoformat()}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_BIRTH_DATE"
        assert await clients_named(db_session, "Lovelace") == []

    async def test_update_rejects_a_future_date_and_changes_nothing(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
    ) -> None:
        client = await make_client_record(psychologist, first_name="Ada")

        response = await api.patch(
            f"/staff/clients/{client.id}",
            json={"first_name": "Changed", "birth_date": "2000-06-16"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_BIRTH_DATE"
        stored = await load_client(db_session, client)
        assert (stored.first_name, stored.birth_date) == ("Ada", None)
        assert await audit_entries(db_session, AuditAction.CLIENT_UPDATED) == []

    async def test_update_accepts_today(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        client = await make_client_record(psychologist)

        response = await api.patch(f"/staff/clients/{client.id}", json={"birth_date": "2000-06-15"})

        assert response.status_code == 200
        assert response.json()["birth_date"] == "2000-06-15"

    async def test_a_colleagues_client_returns_404_before_the_date_is_judged(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        client = await make_client_record(colleague)

        response = await api.patch(f"/staff/clients/{client.id}", json={"birth_date": "2000-06-16"})

        assert response.status_code == 404


async def test_a_new_client_stays_hidden_from_colleagues(
    api: httpx.AsyncClient,
    colleague_api: httpx.AsyncClient,
    psychologist: User,
    db_session: AsyncSession,
) -> None:
    response = await api.post("/staff/clients", json=CLIENT)

    psychologists = await db_session.scalars(
        select(PsychologistClient.psychologist_id).where(
            PsychologistClient.client_id == response.json()["id"]
        )
    )
    assert psychologists.all() == [psychologist.id]
    assert (await colleague_api.get(f"/staff/clients/{response.json()['id']}")).status_code == 404
