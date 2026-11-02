"""Races between requests. Locks and constraints only act between transactions, so these
tests commit for real, on an application with its own connections.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.security import hash_password
from app.modules.catalog.models import DeliveryMode, PsychologistService, Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import (
    ACTIVE_STATUSES,
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    AvailabilityRule,
    BookingChannel,
)
from app.modules.scheduling.repository import SchedulingRepository
from app.modules.scheduling.schemas import (
    BookAppointmentRequest,
    ChangeAppointmentStatusRequest,
    RescheduleAppointmentRequest,
)
from app.modules.scheduling.service import SchedulingService, build_scheduling_service
from app.modules.scheduling.state_machine import InvalidStatusTransitionError
from app.modules.users.actor import Actor
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.modules.users.service import build_user_service
from tests.conftest import (
    DEFAULT_PASSWORD,
    ClientFactory,
    FakeClock,
    SettingsFactory,
    assert_blocked,
    log_in,
)
from tests.scheduling.conftest import NOW, error_code, local

pytestmark = pytest.mark.usefixtures("fixed_time")

ROUNDS = 6
SESSION = timedelta(minutes=50)


@dataclass(frozen=True)
class Practice:
    """A committed psychologist with a service, working every Tuesday."""

    psychologist_id: uuid.UUID
    email: str
    slug: str
    service_id: uuid.UUID


async def committed_user(session: AsyncSession, email: str, role: UserRole) -> User:
    user = User(
        email=email,
        role=role,
        password_hash=await hash_password(DEFAULT_PASSWORD),
        email_verified_at=NOW,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def practice(committed_engine: AsyncEngine) -> Practice:
    async with AsyncSession(committed_engine, expire_on_commit=False) as session:
        user = await committed_user(session, "therapist@example.com", UserRole.PSYCHOLOGIST)
        session.add(
            PsychologistProfile(user_id=user.id, first_name="A", last_name="B", slug="therapist")
        )
        await session.flush()
        service = Service(
            name="Session",
            duration_minutes=50,
            price_amount=Decimal("1500.00"),
            delivery_mode=DeliveryMode.ONLINE,
        )
        session.add(service)
        await session.flush()
        session.add_all(
            [
                PsychologistService(psychologist_id=user.id, service_id=service.id),
                AvailabilityRule(
                    psychologist_id=user.id,
                    weekday=1,
                    start_time=time(9),
                    end_time=time(17),
                    valid_from=date(2020, 1, 1),
                ),
            ]
        )
        await session.commit()
    return Practice(user.id, user.email, "therapist", service.id)


@dataclass(frozen=True)
class CommittedBooker:
    user_id: uuid.UUID
    client_id: uuid.UUID
    api: httpx.AsyncClient


CommittedBookerFactory = Callable[[str], Awaitable[CommittedBooker]]


@pytest.fixture
def make_committed_booker(
    committed_engine: AsyncEngine, new_committed_client: ClientFactory
) -> CommittedBookerFactory:
    async def factory(name: str) -> CommittedBooker:
        email = f"{name}@example.com"
        async with AsyncSession(committed_engine, expire_on_commit=False) as session:
            user = await committed_user(session, email, UserRole.CLIENT)
            client = Client(
                user_id=user.id,
                first_name="Ada",
                last_name=name,
                phone="+905320000000",
                created_by_user_id=user.id,
            )
            session.add(client)
            await session.commit()
        api = await new_committed_client()
        await log_in(api, email)
        return CommittedBooker(user.id, client.id, api)

    return factory


@pytest.fixture
async def first(make_committed_booker: CommittedBookerFactory) -> CommittedBooker:
    return await make_committed_booker("first")


@pytest.fixture
async def second(make_committed_booker: CommittedBookerFactory) -> CommittedBooker:
    return await make_committed_booker("second")


@pytest.fixture
async def therapist_client(
    new_committed_client: ClientFactory, practice: Practice
) -> httpx.AsyncClient:
    client = await new_committed_client()
    await log_in(client, practice.email)
    return client


def body(practice: Practice, starts_at: datetime) -> dict[str, str]:
    return {
        "psychologist_slug": practice.slug,
        "service_id": str(practice.service_id),
        "starts_at": starts_at.isoformat(),
    }


async def set_buffer(engine: AsyncEngine, practice: Practice, minutes: int) -> None:
    async with AsyncSession(engine) as session:
        profile = await session.get(PsychologistProfile, practice.psychologist_id)
        assert profile is not None
        profile.buffer_minutes = minutes
        await session.commit()


async def active_appointments(engine: AsyncEngine) -> list[Appointment]:
    async with AsyncSession(engine) as session:
        result = await session.scalars(
            select(Appointment).where(Appointment.status.in_(ACTIVE_STATUSES))
        )
        return list(result.all())


async def count_history(engine: AsyncEngine, appointment_id: str) -> int:
    async with AsyncSession(engine) as session:
        statement = (
            select(func.count())
            .select_from(AppointmentStatusHistory)
            .where(AppointmentStatusHistory.appointment_id == uuid.UUID(appointment_id))
        )
        return await session.scalar(statement) or 0


def assert_one_won(responses: tuple[httpx.Response, ...], code: str = "SLOT_TAKEN") -> None:
    assert sorted(response.status_code for response in responses) == [201, 409]
    [loser] = [response for response in responses if response.status_code == 409]
    assert error_code(loser) == code


class TestRacesThroughTheApi:
    async def test_two_bookings_of_one_slot_end_with_one_appointment(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        second: CommittedBooker,
    ) -> None:
        for round_number in range(ROUNDS):
            starts_at = local(9 + round_number)

            responses = await asyncio.gather(
                first.api.post("/appointments", json=body(practice, starts_at)),
                second.api.post("/appointments", json=body(practice, starts_at)),
            )

            assert_one_won(responses)

        appointments = await active_appointments(committed_engine)
        assert sorted(item.starts_at for item in appointments) == [
            local(9 + round_number) for round_number in range(ROUNDS)
        ]

    async def test_the_constraint_decides_when_the_check_sees_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        second: CommittedBooker,
    ) -> None:
        async def nothing_booked(*_: object) -> list[tuple[datetime, datetime]]:
            return []

        monkeypatch.setattr(SchedulingRepository, "appointment_ranges", nothing_booked)
        won = await first.api.post("/appointments", json=body(practice, local(10)))

        response = await second.api.post("/appointments", json=body(practice, local(10)))

        assert won.status_code == 201
        assert response.status_code == 409
        assert error_code(response) == "SLOT_TAKEN"
        assert len(await active_appointments(committed_engine)) == 1

    async def test_the_loser_of_the_constraint_leaves_nothing_behind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        second: CommittedBooker,
        therapist_client: httpx.AsyncClient,
    ) -> None:
        async def nothing_booked(*_: object) -> list[tuple[datetime, datetime]]:
            return []

        monkeypatch.setattr(SchedulingRepository, "appointment_ranges", nothing_booked)
        await first.api.post("/appointments", json=body(practice, local(10)))

        await second.api.post("/appointments", json=body(practice, local(10)))

        clients = await therapist_client.get("/staff/clients")
        assert [item["id"] for item in clients.json()["items"]] == [str(first.client_id)]

    async def test_bookings_that_only_break_the_buffer_end_with_one_appointment(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        second: CommittedBooker,
    ) -> None:
        await set_buffer(committed_engine, practice, 30)

        for round_number in range(ROUNDS // 2):
            early = local(9 + 2 * round_number)
            late = early + timedelta(hours=1)

            responses = await asyncio.gather(
                first.api.post("/appointments", json=body(practice, early)),
                second.api.post("/appointments", json=body(practice, late)),
            )

            assert_one_won(responses)

    async def test_a_reschedule_and_a_booking_end_with_one_appointment_on_the_slot(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        second: CommittedBooker,
    ) -> None:
        for round_number in range(ROUNDS // 2):
            origin = local(9 + 2 * round_number)
            contested = origin + timedelta(hours=1)
            booked = await first.api.post("/appointments", json=body(practice, origin))

            moved, taken = await asyncio.gather(
                first.api.post(
                    f"/appointments/{booked.json()['id']}/reschedule",
                    json={"starts_at": contested.isoformat()},
                ),
                second.api.post("/appointments", json=body(practice, contested)),
            )

            assert_one_won((moved, taken))
            active = await active_appointments(committed_engine)
            assert len([item for item in active if item.starts_at == contested]) == 1
            old_is_active = any(str(item.id) == booked.json()["id"] for item in active)
            assert old_is_active is (moved.status_code == 409)

    async def test_two_status_changes_end_with_one_change(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        therapist_client: httpx.AsyncClient,
    ) -> None:
        for round_number in range(ROUNDS):
            booked = await first.api.post(
                "/appointments", json=body(practice, local(9 + round_number))
            )
            appointment_id = booked.json()["id"]

            responses = await asyncio.gather(
                first.api.post(f"/appointments/{appointment_id}/cancel"),
                therapist_client.post(
                    f"/staff/appointments/{appointment_id}/status",
                    json={"status": "cancelled"},
                ),
            )

            assert sorted(response.status_code for response in responses) == [200, 409]
            [loser] = [response for response in responses if response.status_code == 409]
            assert error_code(loser) == "INVALID_STATUS_TRANSITION"
            assert await count_history(committed_engine, appointment_id) == 2

    async def test_a_client_and_the_psychologist_end_with_one_appointment_on_a_slot(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        second: CommittedBooker,
        therapist_client: httpx.AsyncClient,
    ) -> None:
        await first.api.post("/appointments", json=body(practice, local(9)))
        await second.api.post("/appointments", json=body(practice, local(10)))
        for round_number in range(ROUNDS // 2):
            starts_at = local(11 + round_number)
            staff_body = {
                "client_id": str(second.client_id),
                "service_id": str(practice.service_id),
                "starts_at": starts_at.isoformat(),
            }

            responses = await asyncio.gather(
                first.api.post("/appointments", json=body(practice, starts_at)),
                therapist_client.post("/staff/appointments", json=staff_body),
            )

            assert_one_won(responses)

    async def test_a_booking_and_time_off_for_one_slot_do_not_both_happen(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        therapist_client: httpx.AsyncClient,
    ) -> None:
        for round_number in range(ROUNDS):
            starts_at = local(9 + round_number)
            period = {
                "starts_at": starts_at.isoformat(),
                "ends_at": (starts_at + timedelta(minutes=30)).isoformat(),
            }

            booking, time_off = await asyncio.gather(
                first.api.post("/appointments", json=body(practice, starts_at)),
                therapist_client.post("/staff/time-off", json=period),
            )

            assert sorted((booking.status_code, time_off.status_code)) == [201, 409]
            if booking.status_code == 409:
                assert error_code(booking) == "SLOT_UNAVAILABLE"
            else:
                assert error_code(time_off) == "TIME_OFF_CONFLICT"

    async def test_a_booking_and_the_end_of_care_settle_without_a_deadlock(
        self,
        practice: Practice,
        first: CommittedBooker,
        therapist_client: httpx.AsyncClient,
    ) -> None:
        for round_number in range(ROUNDS):
            starts_at = local(9 + round_number)
            booked = await first.api.post("/appointments", json=body(practice, starts_at))
            cancelled = await first.api.post(f"/appointments/{booked.json()['id']}/cancel")
            assert cancelled.status_code == 200

            booking, ending = await asyncio.gather(
                first.api.post("/appointments", json=body(practice, starts_at)),
                therapist_client.delete(f"/staff/clients/{first.client_id}"),
            )

            assert booking.status_code == 201
            assert ending.status_code in {204, 409}
            # Whichever request came first, the booking leaves the client in care.
            care = await therapist_client.get(f"/staff/clients/{first.client_id}")
            assert care.status_code == 200
            cancelled = await first.api.post(f"/appointments/{booking.json()['id']}/cancel")
            assert cancelled.status_code == 200

    async def test_a_reschedule_and_a_cancellation_of_one_appointment_do_not_both_happen(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        therapist_client: httpx.AsyncClient,
    ) -> None:
        booked = await first.api.post("/appointments", json=body(practice, local(9)))
        appointment_id = booked.json()["id"]

        moved, cancelled = await asyncio.gather(
            first.api.post(
                f"/appointments/{appointment_id}/reschedule",
                json={"starts_at": local(11).isoformat()},
            ),
            therapist_client.post(
                f"/staff/appointments/{appointment_id}/status", json={"status": "cancelled"}
            ),
        )

        assert sorted((moved.status_code, cancelled.status_code)) in ([200, 409], [201, 409])
        assert await count_history(committed_engine, appointment_id) == 2


class TestLocks:
    @pytest.fixture
    def settings(self, make_settings: SettingsFactory) -> Settings:
        return make_settings()

    @pytest.fixture
    def build(
        self, settings: Settings, clock: FakeClock
    ) -> Callable[[AsyncSession], SchedulingService]:
        def factory(session: AsyncSession) -> SchedulingService:
            return build_scheduling_service(session, settings, clock)

        return factory

    @pytest.fixture
    async def appointment(
        self, committed_engine: AsyncEngine, practice: Practice, first: CommittedBooker
    ) -> Appointment:
        async with AsyncSession(committed_engine, expire_on_commit=False) as session:
            appointment = Appointment(
                psychologist_id=practice.psychologist_id,
                client_id=first.client_id,
                service_id=practice.service_id,
                starts_at=local(10),
                ends_at=local(10) + SESSION,
                status=AppointmentStatus.CONFIRMED,
                price_amount=Decimal("1500.00"),
                currency="TRY",
                booking_channel=BookingChannel.ONLINE,
                booked_by_user_id=first.user_id,
            )
            session.add(appointment)
            await session.commit()
        return appointment

    async def test_a_status_change_waits_for_another_one(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        appointment: Appointment,
        build: Callable[[AsyncSession], SchedulingService],
    ) -> None:
        actor = Actor(practice.psychologist_id, UserRole.PSYCHOLOGIST)
        request = ChangeAppointmentStatusRequest(status=AppointmentStatus.CANCELLED)
        async with (
            AsyncSession(committed_engine) as winner,
            AsyncSession(committed_engine) as loser,
        ):
            await build(winner).change_status(actor, appointment.id, request, ip_address=None)
            parallel = asyncio.create_task(
                build(loser).change_status(actor, appointment.id, request, ip_address=None)
            )
            await assert_blocked(parallel)

            await winner.commit()
            with pytest.raises(InvalidStatusTransitionError):
                await parallel

    async def test_a_reschedule_waits_for_a_status_change(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        appointment: Appointment,
        build: Callable[[AsyncSession], SchedulingService],
    ) -> None:
        psychologist = Actor(practice.psychologist_id, UserRole.PSYCHOLOGIST)
        client = Actor(first.user_id, UserRole.CLIENT)
        async with (
            AsyncSession(committed_engine) as cancelling,
            AsyncSession(committed_engine) as moving,
        ):
            await build(cancelling).change_status(
                psychologist,
                appointment.id,
                ChangeAppointmentStatusRequest(status=AppointmentStatus.CANCELLED),
                ip_address=None,
            )
            parallel = asyncio.create_task(
                build(moving).reschedule_own_appointment(
                    client,
                    appointment.id,
                    RescheduleAppointmentRequest(starts_at=local(12)),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await cancelling.commit()
            with pytest.raises(InvalidStatusTransitionError):
                await parallel

    async def test_a_buffer_set_during_a_booking_makes_it_exclusive(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        build: Callable[[AsyncSession], SchedulingService],
        clock: FakeClock,
    ) -> None:
        actor = Actor(first.user_id, UserRole.CLIENT)
        request = BookAppointmentRequest(
            psychologist_slug=practice.slug,
            service_id=practice.service_id,
            starts_at=local(10),
        )
        async with (
            AsyncSession(committed_engine) as patching,
            AsyncSession(committed_engine) as booking,
            AsyncSession(committed_engine) as later,
        ):
            users = build_user_service(patching, clock)
            profile = await users.lock_profile(practice.psychologist_id, exclusive=True)
            await users.update_profile(profile, {"buffer_minutes": 30})
            parallel = asyncio.create_task(
                build(booking).book_appointment(actor, request, ip_address=None)
            )
            await assert_blocked(parallel)

            await patching.commit()
            await parallel
            waiting = asyncio.create_task(
                build_user_service(later, clock).lock_profile(
                    practice.psychologist_id, exclusive=False
                )
            )
            await assert_blocked(waiting)

            await booking.commit()
            await waiting


class DriverError(Exception):
    def __init__(self, message: str, sqlstate: str | None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class TestInsertRace:
    @staticmethod
    def appointment(
        practice: Practice, booker: CommittedBooker, starts_at: datetime, **fields: object
    ) -> Appointment:
        values: dict[str, object] = {
            "psychologist_id": practice.psychologist_id,
            "client_id": booker.client_id,
            "service_id": practice.service_id,
            "starts_at": starts_at,
            "ends_at": starts_at + SESSION,
            "status": AppointmentStatus.CONFIRMED,
            "price_amount": Decimal("1500.00"),
            "currency": "TRY",
            "booking_channel": BookingChannel.ONLINE,
            "booked_by_user_id": booker.user_id,
        }
        return Appointment(**(values | fields))

    async def test_a_deadlock_between_two_inserts_counts_as_a_lost_race(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
    ) -> None:
        async def attempt(session: AsyncSession, appointment: Appointment) -> bool:
            try:
                return await SchedulingRepository(session).insert_appointment(appointment)
            finally:
                # The other transaction waits for this one, even after this insert failed.
                await session.rollback()

        async with (
            AsyncSession(committed_engine) as one,
            AsyncSession(committed_engine) as other,
        ):
            early, late = local(9), local(11)
            assert await SchedulingRepository(one).insert_appointment(
                self.appointment(practice, first, early)
            )
            assert await SchedulingRepository(other).insert_appointment(
                self.appointment(practice, first, late)
            )

            # Each insert now waits for the uncommitted row of the other transaction.
            outcomes = await asyncio.gather(
                attempt(one, self.appointment(practice, first, late)),
                attempt(other, self.appointment(practice, first, early)),
                return_exceptions=True,
            )

        assert sorted(outcomes, key=str) == [False, True]

    async def test_a_lost_race_leaves_the_transaction_usable(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
    ) -> None:
        async with AsyncSession(committed_engine) as session:
            repository = SchedulingRepository(session)
            assert await repository.insert_appointment(self.appointment(practice, first, local(9)))

            lost = await repository.insert_appointment(self.appointment(practice, first, local(9)))

            assert lost is False
            statement = select(func.count()).select_from(Appointment)
            assert await session.scalar(statement) == 1

    @pytest.mark.parametrize(
        ("sqlstate", "message"),
        [
            ("23P01", 'violates exclusion constraint "ex_other"'),
            ("23505", 'violates unique constraint "ex_appointments_no_overlap"'),
            (None, "connection lost"),
        ],
        ids=["other_exclusion_constraint", "other_sqlstate", "no_sqlstate"],
    )
    async def test_only_the_slot_constraint_counts_as_a_lost_race(
        self,
        monkeypatch: pytest.MonkeyPatch,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
        sqlstate: str | None,
        message: str,
    ) -> None:
        async def fail(*_: object) -> None:
            raise DBAPIError("INSERT", {}, DriverError(message, sqlstate))

        async with AsyncSession(committed_engine) as session:
            monkeypatch.setattr(session, "flush", fail)

            with pytest.raises(DBAPIError):
                await SchedulingRepository(session).insert_appointment(
                    self.appointment(practice, first, local(9))
                )

    async def test_other_database_errors_are_not_mistaken_for_a_lost_race(
        self,
        committed_engine: AsyncEngine,
        practice: Practice,
        first: CommittedBooker,
    ) -> None:
        invalid = self.appointment(practice, first, local(9), currency="xx")

        async with AsyncSession(committed_engine) as session:
            with pytest.raises(IntegrityError):
                await SchedulingRepository(session).insert_appointment(invalid)
