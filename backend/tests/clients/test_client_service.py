import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.pagination import PageParams
from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditAction
from app.modules.catalog.models import DeliveryMode, Service
from app.modules.clients.models import Client, PsychologistClient
from app.modules.clients.schemas import CreateClientRequest, UpdateClientRequest
from app.modules.clients.service import (
    ClientHasActiveAppointmentsError,
    ClientName,
    ClientService,
    build_client_service,
)
from app.modules.scheduling.models import Appointment, AppointmentStatus, BookingChannel
from app.modules.scheduling.service import build_appointment_lookup
from app.modules.users.actor import Actor, ForbiddenError
from app.modules.users.models import PsychologistProfile, User, UserRole
from tests.clients.conftest import load_client, load_relationship
from tests.conftest import (
    LOCK_WAIT_SECONDS,
    UNKNOWN_ID,
    ClientRecordFactory,
    FakeClock,
    SettingsFactory,
    UserFactory,
    assert_blocked,
    audit_entries,
)

ClientServiceFactory = Callable[[AsyncSession], ClientService]


class NoAppointments:
    async def has_upcoming_appointments(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> bool:
        return False


class AlwaysBusy:
    async def has_upcoming_appointments(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> bool:
        return True


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings()


@pytest.fixture
def clients(db_session: AsyncSession, settings: Settings, clock: FakeClock) -> ClientService:
    return build_client_service(db_session, settings, clock)


@pytest.fixture
async def therapist(make_user: UserFactory) -> User:
    return await make_user(UserRole.PSYCHOLOGIST)


class TestGetForUser:
    async def test_returns_the_record_of_the_account(
        self,
        clients: ClientService,
        therapist: User,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
    ) -> None:
        account = await make_user()
        record = await make_client_record(therapist, user_id=account.id)

        found = await clients.get_for_user(account.id)

        assert found is not None
        assert found.id == record.id

    async def test_returns_a_soft_deleted_record_so_that_the_client_can_book_again(
        self,
        clients: ClientService,
        therapist: User,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
        clock: FakeClock,
    ) -> None:
        account = await make_user()
        record = await make_client_record(therapist, user_id=account.id, deleted_at=clock.now)

        found = await clients.get_for_user(account.id)

        assert found is not None
        assert found.id == record.id

    async def test_is_none_without_a_record(self, clients: ClientService) -> None:
        assert await clients.get_for_user(UNKNOWN_ID) is None


class TestEnsureCareRelationship:
    async def test_creates_a_missing_relationship(
        self,
        clients: ClientService,
        therapist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
    ) -> None:
        record = await make_client_record(colleague)

        await clients.ensure_care_relationship(therapist.id, record.id)

        assert (await load_relationship(db_session, therapist, record)).ended_at is None

    async def test_leaves_an_active_relationship_active(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
    ) -> None:
        record = await make_client_record(therapist)

        await clients.ensure_care_relationship(therapist.id, record.id)

        assert (await load_relationship(db_session, therapist, record)).ended_at is None

    async def test_reopens_an_ended_relationship(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
        clock: FakeClock,
    ) -> None:
        record = await make_client_record(therapist)
        relationship = await load_relationship(db_session, therapist, record)
        relationship.ended_at = clock.now
        await db_session.commit()

        await clients.ensure_care_relationship(therapist.id, record.id)

        assert (await load_relationship(db_session, therapist, record)).ended_at is None

    async def test_restores_a_soft_deleted_client(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
        clock: FakeClock,
    ) -> None:
        record = await make_client_record(therapist, deleted_at=clock.now)
        relationship = await load_relationship(db_session, therapist, record)
        relationship.ended_at = clock.now
        await db_session.commit()

        await clients.ensure_care_relationship(therapist.id, record.id)

        assert (await load_client(db_session, record)).deleted_at is None
        assert (await load_relationship(db_session, therapist, record)).ended_at is None
        [entry] = await audit_entries(db_session, AuditAction.CLIENT_UPDATED)
        assert entry.actor_user_id is None
        assert entry.entity_type == "client"
        assert entry.entity_id == record.id
        assert entry.event_metadata == {"changed_fields": ["deleted_at"]}

    async def test_a_client_that_was_not_deleted_adds_no_audit_entry(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
        clock: FakeClock,
    ) -> None:
        record = await make_client_record(therapist)
        relationship = await load_relationship(db_session, therapist, record)
        relationship.ended_at = clock.now
        await db_session.commit()

        await clients.ensure_care_relationship(therapist.id, record.id)

        assert await audit_entries(db_session, AuditAction.CLIENT_UPDATED) == []

    async def test_an_unknown_client_is_not_found(
        self, clients: ClientService, therapist: User
    ) -> None:
        with pytest.raises(NotFoundError):
            await clients.ensure_care_relationship(therapist.id, UNKNOWN_ID)


class TestHasCareRelationshipRow:
    async def test_is_true_for_an_active_relationship(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        record = await make_client_record(therapist)

        assert await clients.has_care_relationship_row(therapist.id, record.id) is True

    async def test_is_true_for_an_ended_relationship_of_a_deleted_client(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
        db_session: AsyncSession,
        clock: FakeClock,
    ) -> None:
        record = await make_client_record(therapist, deleted_at=clock.now)
        relationship = await load_relationship(db_session, therapist, record)
        relationship.ended_at = clock.now
        await db_session.commit()

        assert await clients.has_care_relationship_row(therapist.id, record.id) is True

    async def test_is_false_for_a_colleagues_client(
        self,
        clients: ClientService,
        therapist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        record = await make_client_record(colleague)

        assert await clients.has_care_relationship_row(therapist.id, record.id) is False

    async def test_is_false_for_an_unknown_client(
        self, clients: ClientService, therapist: User
    ) -> None:
        assert await clients.has_care_relationship_row(therapist.id, UNKNOWN_ID) is False


class TestGetDisplayNames:
    async def test_includes_deleted_clients_and_skips_unknown_ids(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
        clock: FakeClock,
    ) -> None:
        active = await make_client_record(therapist, first_name="Ada", last_name="Lovelace")
        deleted = await make_client_record(
            therapist, first_name="Alan", last_name="Turing", deleted_at=clock.now
        )

        names = await clients.get_display_names([active.id, deleted.id, UNKNOWN_ID])

        assert names == {
            active.id: ClientName(first_name="Ada", last_name="Lovelace"),
            deleted.id: ClientName(first_name="Alan", last_name="Turing"),
        }

    async def test_of_no_ids_is_empty(self, clients: ClientService) -> None:
        assert await clients.get_display_names([]) == {}


class TestWithoutARouter:
    REQUEST = CreateClientRequest(
        first_name="Ada",
        last_name="Lovelace",
        phone="+90 532 123 45 67",
        kvkk_disclosure_given=True,
    )

    async def test_reads_require_a_staff_role(self, clients: ClientService) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=UserRole.CLIENT)

        with pytest.raises(ForbiddenError):
            await clients.list_clients(actor, PageParams())
        with pytest.raises(ForbiddenError):
            await clients.get_client(actor, UNKNOWN_ID)

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
    async def test_writes_require_the_psychologist_role(
        self, clients: ClientService, role: UserRole
    ) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=role)

        calls = [
            clients.create_client(actor, self.REQUEST, ip_address=None),
            clients.update_client(actor, UNKNOWN_ID, UpdateClientRequest(), ip_address=None),
            clients.end_care(actor, UNKNOWN_ID, NoAppointments(), ip_address=None),
        ]

        for call in calls:
            with pytest.raises(ForbiddenError):
                await call

    async def test_psychologists_are_scoped_to_their_own_care(
        self,
        clients: ClientService,
        therapist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        record = await make_client_record(colleague)
        actor = Actor(user_id=therapist.id, role=UserRole.PSYCHOLOGIST)

        assert await clients.list_clients(actor, PageParams()) == ([], 0)
        with pytest.raises(NotFoundError):
            await clients.get_client(actor, record.id)
        with pytest.raises(NotFoundError):
            await clients.update_client(
                actor, record.id, UpdateClientRequest(first_name="New"), ip_address=None
            )
        with pytest.raises(NotFoundError):
            await clients.end_care(actor, record.id, NoAppointments(), ip_address=None)

    async def test_ending_care_obeys_the_lookup_it_receives(
        self,
        clients: ClientService,
        therapist: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        record = await make_client_record(therapist)
        actor = Actor(user_id=therapist.id, role=UserRole.PSYCHOLOGIST)

        with pytest.raises(ClientHasActiveAppointmentsError):
            await clients.end_care(actor, record.id, AlwaysBusy(), ip_address=None)


@dataclass(frozen=True)
class SharedCare:
    first: Actor
    second: Actor
    client_id: uuid.UUID
    service_id: uuid.UUID


class TestCareLocks:
    """Row locks only act between transactions, so these tests commit for real."""

    @pytest.fixture
    def build(self, settings: Settings) -> ClientServiceFactory:
        def factory(session: AsyncSession) -> ClientService:
            return build_client_service(session, settings, utcnow)

        return factory

    @pytest.fixture
    async def care(self, committed_engine: AsyncEngine) -> SharedCare:
        async with AsyncSession(committed_engine, expire_on_commit=False) as session:
            first = await self.committed_psychologist(session, "locks-first")
            second = await self.committed_psychologist(session, "locks-second")
            client = Client(
                first_name="A", last_name="B", phone="+905320000000", created_by_user_id=first.id
            )
            service = Service(
                name="Session",
                duration_minutes=50,
                price_amount=Decimal("1500.00"),
                delivery_mode=DeliveryMode.ONLINE,
            )
            session.add_all([client, service])
            await session.flush()
            session.add_all(
                PsychologistClient(psychologist_id=user.id, client_id=client.id)
                for user in (first, second)
            )
            await session.commit()
        return SharedCare(
            Actor(first.id, UserRole.PSYCHOLOGIST),
            Actor(second.id, UserRole.PSYCHOLOGIST),
            client.id,
            service.id,
        )

    @staticmethod
    async def committed_psychologist(session: AsyncSession, slug: str) -> User:
        user = User(email=f"{slug}@example.com", role=UserRole.PSYCHOLOGIST)
        session.add(user)
        await session.flush()
        session.add(PsychologistProfile(user_id=user.id, first_name="A", last_name="B", slug=slug))
        await session.flush()
        return user

    @staticmethod
    def appointment(care: SharedCare, actor: Actor) -> Appointment:
        starts_at = utcnow() + timedelta(days=1)
        return Appointment(
            psychologist_id=actor.user_id,
            client_id=care.client_id,
            service_id=care.service_id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=50),
            status=AppointmentStatus.CONFIRMED,
            price_amount=Decimal("1500.00"),
            currency="TRY",
            booking_channel=BookingChannel.STAFF,
            booked_by_user_id=actor.user_id,
        )

    @staticmethod
    async def relationship_ended(session: AsyncSession, actor: Actor, care: SharedCare) -> bool:
        ended_at = await session.scalar(
            select(PsychologistClient.ended_at).where(
                PsychologistClient.psychologist_id == actor.user_id,
                PsychologistClient.client_id == care.client_id,
            )
        )
        return ended_at is not None

    @staticmethod
    async def client_deleted(session: AsyncSession, care: SharedCare) -> bool:
        deleted_at = await session.scalar(
            select(Client.deleted_at).where(Client.id == care.client_id)
        )
        return deleted_at is not None

    async def test_two_psychologists_ending_at_once_still_delete_the_client(
        self, committed_engine: AsyncEngine, care: SharedCare, build: ClientServiceFactory
    ) -> None:
        async with (
            AsyncSession(committed_engine) as first,
            AsyncSession(committed_engine) as second,
        ):
            await build(first).end_care(
                care.first, care.client_id, NoAppointments(), ip_address=None
            )
            parallel = asyncio.create_task(
                build(second).end_care(
                    care.second, care.client_id, NoAppointments(), ip_address=None
                )
            )
            await assert_blocked(parallel)

            await first.commit()
            await parallel
            await second.commit()

        async with AsyncSession(committed_engine) as session:
            assert await self.client_deleted(session, care) is True
            entries = await session.scalars(
                select(AuditLog.event_metadata).where(AuditLog.entity_id == care.client_id)
            )
            assert sorted(item["client_deleted"] for item in entries) == [False, True]

    async def test_ending_twice_at_once_ends_care_once(
        self, committed_engine: AsyncEngine, care: SharedCare, build: ClientServiceFactory
    ) -> None:
        async with (
            AsyncSession(committed_engine) as first,
            AsyncSession(committed_engine) as second,
        ):
            await build(first).end_care(
                care.first, care.client_id, NoAppointments(), ip_address=None
            )
            parallel = asyncio.create_task(
                build(second).end_care(
                    care.first, care.client_id, NoAppointments(), ip_address=None
                )
            )
            await assert_blocked(parallel)

            await first.commit()
            with pytest.raises(NotFoundError):
                await parallel

    async def test_ending_care_waits_for_a_booking_in_flight(
        self, committed_engine: AsyncEngine, care: SharedCare, build: ClientServiceFactory
    ) -> None:
        async with (
            AsyncSession(committed_engine) as booking,
            AsyncSession(committed_engine) as ending,
        ):
            booking.add(self.appointment(care, care.first))
            await booking.flush()
            parallel = asyncio.create_task(
                build(ending).end_care(
                    care.first,
                    care.client_id,
                    build_appointment_lookup(ending, utcnow),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await booking.commit()
            with pytest.raises(ClientHasActiveAppointmentsError):
                await parallel

    async def test_reopening_during_an_ending_leaves_the_client_restored(
        self, committed_engine: AsyncEngine, care: SharedCare, build: ClientServiceFactory
    ) -> None:
        async with AsyncSession(committed_engine) as setup:
            await build(setup).end_care(
                care.second, care.client_id, NoAppointments(), ip_address=None
            )
            await setup.commit()

        async with (
            AsyncSession(committed_engine) as ending,
            AsyncSession(committed_engine) as reopening,
        ):
            await build(ending).end_care(
                care.first, care.client_id, NoAppointments(), ip_address=None
            )
            parallel = asyncio.create_task(
                build(reopening).ensure_care_relationship(care.second.user_id, care.client_id)
            )
            await assert_blocked(parallel)

            await ending.commit()
            await parallel
            await reopening.commit()

        async with AsyncSession(committed_engine) as session:
            assert await self.client_deleted(session, care) is False
            assert await self.relationship_ended(session, care.second, care) is False
            assert await self.relationship_ended(session, care.first, care) is True

    async def test_parallel_bookings_of_one_client_do_not_deadlock(
        self, committed_engine: AsyncEngine, care: SharedCare, build: ClientServiceFactory
    ) -> None:
        async with (
            AsyncSession(committed_engine) as first,
            AsyncSession(committed_engine) as second,
        ):
            first.add(self.appointment(care, care.first))
            await first.flush()
            second.add(self.appointment(care, care.second))
            await second.flush()

            await asyncio.wait_for(
                build(first).ensure_care_relationship(care.first.user_id, care.client_id),
                timeout=LOCK_WAIT_SECONDS * 10,
            )
            parallel = asyncio.create_task(
                build(second).ensure_care_relationship(care.second.user_id, care.client_id)
            )
            await assert_blocked(parallel)

            await first.commit()
            await parallel
            await second.commit()
