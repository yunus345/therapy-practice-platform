from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.models import Client
from app.modules.clients.service import AppointmentLookup
from app.modules.scheduling.models import AppointmentStatus, CancellationReason
from app.modules.scheduling.service import build_appointment_lookup
from app.modules.users.models import User, UserRole
from tests.conftest import AppointmentFactory, ClientRecordFactory, FakeClock, UserFactory


@pytest.fixture
def lookup(db_session: AsyncSession, clock: FakeClock) -> AppointmentLookup:
    return build_appointment_lookup(db_session, clock)


@pytest.fixture
async def therapist(make_user: UserFactory) -> User:
    return await make_user(UserRole.PSYCHOLOGIST)


@pytest.fixture
async def record(therapist: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(therapist)


async def test_no_appointments_means_nothing_upcoming(
    lookup: AppointmentLookup, therapist: User, record: Client
) -> None:
    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is False


@pytest.mark.parametrize("status", [AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
async def test_an_active_appointment_in_the_future_is_upcoming(
    lookup: AppointmentLookup,
    therapist: User,
    record: Client,
    make_appointment: AppointmentFactory,
    status: AppointmentStatus,
) -> None:
    await make_appointment(therapist, record, status=status)

    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is True


async def test_an_appointment_in_progress_is_upcoming(
    lookup: AppointmentLookup,
    therapist: User,
    record: Client,
    make_appointment: AppointmentFactory,
    clock: FakeClock,
) -> None:
    await make_appointment(therapist, record, starts_at=clock.now - timedelta(minutes=49))

    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is True


async def test_an_appointment_stops_being_upcoming_the_moment_it_ends(
    lookup: AppointmentLookup,
    therapist: User,
    record: Client,
    make_appointment: AppointmentFactory,
    clock: FakeClock,
) -> None:
    appointment = await make_appointment(therapist, record)

    clock.now = appointment.ends_at - timedelta(microseconds=1)
    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is True

    clock.now = appointment.ends_at
    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is False


@pytest.mark.parametrize(
    "fields",
    [
        {"status": AppointmentStatus.COMPLETED},
        {"status": AppointmentStatus.NO_SHOW},
        {
            "status": AppointmentStatus.CANCELLED,
            "cancellation_reason": CancellationReason.PSYCHOLOGIST_REQUEST,
        },
    ],
    ids=["completed", "no_show", "cancelled"],
)
async def test_a_closed_appointment_is_not_upcoming(
    lookup: AppointmentLookup,
    therapist: User,
    record: Client,
    make_appointment: AppointmentFactory,
    clock: FakeClock,
    fields: dict[str, Any],
) -> None:
    cancelled = fields["status"] is AppointmentStatus.CANCELLED
    extra = {"cancelled_at": clock.now} if cancelled else {}
    await make_appointment(therapist, record, **(fields | extra))

    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is False


async def test_an_appointment_with_a_colleague_does_not_count(
    lookup: AppointmentLookup,
    therapist: User,
    record: Client,
    make_user: UserFactory,
    make_appointment: AppointmentFactory,
) -> None:
    await make_appointment(await make_user(UserRole.PSYCHOLOGIST), record)

    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is False


async def test_an_appointment_of_another_client_does_not_count(
    lookup: AppointmentLookup,
    therapist: User,
    record: Client,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
) -> None:
    await make_appointment(therapist, await make_client_record(therapist))

    assert await lookup.has_upcoming_appointments(therapist.id, record.id) is False
