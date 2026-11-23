from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.clients.models import Client
from app.modules.scheduling.models import AppointmentStatus, CancellationReason
from app.modules.users.models import User, UserRole
from tests.clients.conftest import load_client, load_relationship
from tests.conftest import (
    UNKNOWN_ID,
    AppointmentFactory,
    ClientFactory,
    ClientRecordFactory,
    FakeClock,
    UserFactory,
    audit_entries,
    log_in,
)


async def assert_care_is_intact(
    session: AsyncSession, psychologist: User, client: Client, *, audited: int = 0
) -> None:
    assert (await load_relationship(session, psychologist, client)).ended_at is None
    assert (await load_client(session, client)).deleted_at is None
    assert len(await audit_entries(session, AuditAction.CLIENT_CARE_ENDED)) == audited


async def test_ending_the_only_relationship_soft_deletes_the_client(
    api: httpx.AsyncClient,
    psychologist: User,
    make_client_record: ClientRecordFactory,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    client = await make_client_record(psychologist)

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert (await load_relationship(db_session, psychologist, client)).ended_at == clock.now
    assert (await load_client(db_session, client)).deleted_at == clock.now


async def test_a_shared_client_stays_for_the_colleague(
    api: httpx.AsyncClient,
    colleague_api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    client = await make_client_record(psychologist, colleague)

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 204
    assert (await load_relationship(db_session, psychologist, client)).ended_at == clock.now
    assert (await load_relationship(db_session, colleague, client)).ended_at is None
    assert (await load_client(db_session, client)).deleted_at is None
    assert (await colleague_api.get(f"/staff/clients/{client.id}")).status_code == 200


async def test_the_last_psychologist_to_end_care_soft_deletes_a_shared_client(
    api: httpx.AsyncClient,
    colleague_api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    client = await make_client_record(psychologist, colleague)
    await api.delete(f"/staff/clients/{client.id}")
    clock.advance(minutes=30)

    response = await colleague_api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 204
    assert (await load_client(db_session, client)).deleted_at == clock.now


async def test_an_ended_relationship_hides_the_client_from_the_psychologist(
    api: httpx.AsyncClient,
    colleague_api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
) -> None:
    client = await make_client_record(psychologist, colleague)
    url = f"/staff/clients/{client.id}"
    await api.delete(url)

    listed = await api.get("/staff/clients")
    read = await api.get(url)
    updated = await api.patch(url, json={"first_name": "Changed"})
    ended_again = await api.delete(url)

    assert listed.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert (read.status_code, updated.status_code, ended_again.status_code) == (404, 404, 404)
    assert (await colleague_api.get(url)).json()["first_name"] == "Ada"


async def test_a_soft_deleted_client_disappears_for_admins_too(
    api: httpx.AsyncClient,
    new_client: ClientFactory,
    make_user: UserFactory,
    psychologist: User,
    make_client_record: ClientRecordFactory,
) -> None:
    client = await make_client_record(psychologist)
    admin_api = await new_client()
    await log_in(admin_api, (await make_user(UserRole.ADMIN)).email)
    assert (await admin_api.get(f"/staff/clients/{client.id}")).status_code == 200

    await api.delete(f"/staff/clients/{client.id}")

    assert (await admin_api.get(f"/staff/clients/{client.id}")).status_code == 404
    assert (await admin_api.get("/staff/clients")).json()["total"] == 0


async def test_admin_keeps_seeing_a_client_while_a_psychologist_remains(
    api: httpx.AsyncClient,
    new_client: ClientFactory,
    make_user: UserFactory,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
) -> None:
    client = await make_client_record(psychologist, colleague)
    admin_api = await new_client()
    await log_in(admin_api, (await make_user(UserRole.ADMIN)).email)

    await api.delete(f"/staff/clients/{client.id}")

    assert (await admin_api.get(f"/staff/clients/{client.id}")).status_code == 200


@pytest.mark.parametrize("status", [AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
async def test_an_upcoming_appointment_blocks_the_ending_and_changes_nothing(
    api: httpx.AsyncClient,
    psychologist: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
    db_session: AsyncSession,
    status: AppointmentStatus,
) -> None:
    client = await make_client_record(psychologist)
    await make_appointment(psychologist, client, status=status)

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLIENT_HAS_ACTIVE_APPOINTMENTS"
    await assert_care_is_intact(db_session, psychologist, client)


async def test_an_appointment_in_progress_blocks_the_ending(
    api: httpx.AsyncClient,
    psychologist: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
    clock: FakeClock,
) -> None:
    client = await make_client_record(psychologist)
    await make_appointment(psychologist, client, starts_at=clock.now - timedelta(minutes=10))

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 409


async def test_the_block_lifts_when_the_appointment_ends(
    api: httpx.AsyncClient,
    psychologist: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
    clock: FakeClock,
) -> None:
    client = await make_client_record(psychologist)
    starts_at = clock.now - timedelta(minutes=30)
    appointment = await make_appointment(psychologist, client, starts_at=starts_at)
    assert (await api.delete(f"/staff/clients/{client.id}")).status_code == 409

    clock.now = appointment.ends_at
    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 204


@pytest.mark.parametrize(
    "fields",
    [
        {"status": AppointmentStatus.COMPLETED},
        {"status": AppointmentStatus.NO_SHOW},
        {
            "status": AppointmentStatus.CANCELLED,
            "cancellation_reason": CancellationReason.CLIENT_REQUEST,
        },
    ],
    ids=["completed", "no_show", "cancelled"],
)
async def test_a_closed_appointment_does_not_block_the_ending(
    api: httpx.AsyncClient,
    psychologist: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
    clock: FakeClock,
    fields: dict[str, Any],
) -> None:
    client = await make_client_record(psychologist)
    cancelled = fields["status"] is AppointmentStatus.CANCELLED
    extra = {"cancelled_at": clock.now} if cancelled else {}
    await make_appointment(psychologist, client, **(fields | extra))

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 204


async def test_only_the_psychologist_with_the_appointment_is_blocked(
    api: httpx.AsyncClient,
    colleague_api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
    db_session: AsyncSession,
) -> None:
    client = await make_client_record(psychologist, colleague)
    await make_appointment(colleague, client)

    allowed = await api.delete(f"/staff/clients/{client.id}")
    blocked = await colleague_api.delete(f"/staff/clients/{client.id}")

    assert (allowed.status_code, blocked.status_code) == (204, 409)
    assert (await load_relationship(db_session, psychologist, client)).ended_at is not None
    await assert_care_is_intact(db_session, colleague, client, audited=1)


async def test_an_appointment_with_another_client_does_not_block_the_ending(
    api: httpx.AsyncClient,
    psychologist: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
) -> None:
    client = await make_client_record(psychologist)
    await make_appointment(psychologist, await make_client_record(psychologist))

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 204


async def test_ending_care_of_a_colleagues_client_returns_404_and_changes_nothing(
    api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
    db_session: AsyncSession,
) -> None:
    client = await make_client_record(colleague)

    forbidden = await api.delete(f"/staff/clients/{client.id}")
    unknown = await api.delete(f"/staff/clients/{UNKNOWN_ID}")

    assert forbidden.status_code == 404
    assert forbidden.json() == unknown.json()
    await assert_care_is_intact(db_session, colleague, client)


async def test_a_colleagues_appointment_is_not_revealed_by_an_unrelated_psychologist(
    api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
) -> None:
    client = await make_client_record(colleague)
    await make_appointment(colleague, client)

    response = await api.delete(f"/staff/clients/{client.id}")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("shared", "client_deleted"), [(False, True), (True, False)], ids=["last", "shared"]
)
async def test_ending_care_is_audited_with_identifiers_only(
    api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
    db_session: AsyncSession,
    shared: bool,
    client_deleted: bool,
) -> None:
    others = (colleague,) if shared else ()
    client = await make_client_record(psychologist, *others)

    await api.delete(f"/staff/clients/{client.id}")

    [entry] = await audit_entries(db_session, AuditAction.CLIENT_CARE_ENDED)
    assert entry.actor_user_id == psychologist.id
    assert entry.entity_type == "client"
    assert entry.entity_id == client.id
    assert str(entry.ip_address) == "127.0.0.1"
    assert entry.event_metadata == {"client_deleted": client_deleted}
