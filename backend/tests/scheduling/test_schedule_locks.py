"""Row locks only act between transactions, so these tests commit for real."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.modules.catalog.models import DeliveryMode, Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AvailabilityRule,
    BookingChannel,
    TimeOff,
)
from app.modules.scheduling.schemas import (
    CreateTimeOffRequest,
    UpdateAvailabilityRuleRequest,
    UpdateTimeOffRequest,
)
from app.modules.scheduling.service import (
    InvalidTimeRangeError,
    SchedulingService,
    TimeOffConflictError,
    build_scheduling_service,
)
from app.modules.users.actor import Actor
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.modules.users.service import build_user_service
from tests.conftest import SettingsFactory, assert_blocked
from tests.scheduling.conftest import PERIOD_END, PERIOD_START


def on_tuesday(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 12, 8, hour, minute, tzinfo=UTC)


@dataclass(frozen=True)
class CommittedSchedule:
    actor: Actor
    client_id: uuid.UUID
    service_id: uuid.UUID
    period_id: uuid.UUID
    rule_id: uuid.UUID


SchedulingServiceFactory = Callable[[AsyncSession], SchedulingService]


class TestScheduleLocks:
    @pytest.fixture
    def settings(self, make_settings: SettingsFactory) -> Settings:
        return make_settings()

    @pytest.fixture
    def build(self, settings: Settings) -> SchedulingServiceFactory:
        def factory(session: AsyncSession) -> SchedulingService:
            return build_scheduling_service(session, settings, utcnow)

        return factory

    @pytest.fixture
    async def schedule(self, committed_engine: AsyncEngine) -> CommittedSchedule:
        async with AsyncSession(committed_engine, expire_on_commit=False) as session:
            user = User(email="locks@example.com", role=UserRole.PSYCHOLOGIST)
            session.add(user)
            await session.flush()
            session.add(
                PsychologistProfile(user_id=user.id, first_name="A", last_name="B", slug="locks")
            )
            await session.flush()
            client = Client(
                first_name="A", last_name="B", phone="+905320000000", created_by_user_id=user.id
            )
            service = Service(
                name="Session",
                duration_minutes=50,
                price_amount=Decimal("1500.00"),
                delivery_mode=DeliveryMode.ONLINE,
            )
            period = TimeOff(
                psychologist_id=user.id, starts_at=on_tuesday(5), ends_at=on_tuesday(7)
            )
            rule = AvailabilityRule(
                psychologist_id=user.id,
                weekday=1,
                start_time=time(9),
                end_time=time(17),
                valid_from=date(2026, 12, 1),
            )
            session.add_all([client, service, period, rule])
            await session.commit()
        return CommittedSchedule(
            Actor(user.id, UserRole.PSYCHOLOGIST), client.id, service.id, period.id, rule.id
        )

    @staticmethod
    def appointment(schedule: CommittedSchedule) -> Appointment:
        return Appointment(
            psychologist_id=schedule.actor.user_id,
            client_id=schedule.client_id,
            service_id=schedule.service_id,
            starts_at=PERIOD_START,
            ends_at=PERIOD_START + timedelta(minutes=50),
            status=AppointmentStatus.CONFIRMED,
            price_amount=Decimal("1500.00"),
            currency="TRY",
            booking_channel=BookingChannel.ONLINE,
            booked_by_user_id=schedule.actor.user_id,
        )

    @staticmethod
    def period_over_appointment() -> CreateTimeOffRequest:
        return CreateTimeOffRequest(starts_at=PERIOD_START, ends_at=PERIOD_END)

    async def test_new_time_off_waits_for_a_booking_in_flight(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as booking,
            AsyncSession(committed_engine) as blocking,
        ):
            users = build_user_service(booking, utcnow)
            await users.lock_profile(schedule.actor.user_id, exclusive=False)
            booking.add(self.appointment(schedule))
            await booking.flush()
            parallel = asyncio.create_task(
                build(blocking).create_time_off(
                    schedule.actor, self.period_over_appointment(), ip_address=None
                )
            )
            await assert_blocked(parallel)

            await booking.commit()
            with pytest.raises(TimeOffConflictError) as error:
                await parallel

        assert error.value.details == {"appointment_count": 1}

    async def test_moving_time_off_waits_for_a_booking_in_flight(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as booking,
            AsyncSession(committed_engine) as moving,
        ):
            users = build_user_service(booking, utcnow)
            await users.lock_profile(schedule.actor.user_id, exclusive=False)
            booking.add(self.appointment(schedule))
            await booking.flush()
            parallel = asyncio.create_task(
                build(moving).update_time_off(
                    schedule.actor,
                    schedule.period_id,
                    UpdateTimeOffRequest(ends_at=PERIOD_END),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await booking.commit()
            with pytest.raises(TimeOffConflictError):
                await parallel

    async def test_a_booking_waits_for_time_off_in_flight(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as blocking,
            AsyncSession(committed_engine) as booking,
        ):
            await build(blocking).create_time_off(
                schedule.actor, self.period_over_appointment(), ip_address=None
            )
            parallel = asyncio.create_task(
                build_user_service(booking, utcnow).lock_profile(
                    schedule.actor.user_id, exclusive=False
                )
            )
            await assert_blocked(parallel)

            await blocking.commit()
            await parallel

    async def test_two_partial_updates_of_a_period_cannot_break_its_range(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as first,
            AsyncSession(committed_engine) as second,
        ):
            await build(first).update_time_off(
                schedule.actor,
                schedule.period_id,
                UpdateTimeOffRequest(starts_at=on_tuesday(6, 30)),
                ip_address=None,
            )
            parallel = asyncio.create_task(
                build(second).update_time_off(
                    schedule.actor,
                    schedule.period_id,
                    UpdateTimeOffRequest(ends_at=on_tuesday(6)),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await first.commit()
            with pytest.raises(InvalidTimeRangeError):
                await parallel

    async def test_two_partial_updates_of_a_rule_cannot_break_its_range(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as first,
            AsyncSession(committed_engine) as second,
        ):
            await build(first).update_rule(
                schedule.actor,
                schedule.rule_id,
                UpdateAvailabilityRuleRequest(start_time=time(12)),
                ip_address=None,
            )
            parallel = asyncio.create_task(
                build(second).update_rule(
                    schedule.actor,
                    schedule.rule_id,
                    UpdateAvailabilityRuleRequest(end_time=time(10)),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await first.commit()
            with pytest.raises(InvalidTimeRangeError):
                await parallel

    async def test_updating_a_rule_being_deleted_is_not_found(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as deleting,
            AsyncSession(committed_engine) as updating,
        ):
            await build(deleting).delete_rule(schedule.actor, schedule.rule_id, ip_address=None)
            parallel = asyncio.create_task(
                build(updating).update_rule(
                    schedule.actor,
                    schedule.rule_id,
                    UpdateAvailabilityRuleRequest(end_time=time(16)),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await deleting.commit()
            with pytest.raises(NotFoundError):
                await parallel

    async def test_updating_a_period_being_deleted_is_not_found(
        self,
        committed_engine: AsyncEngine,
        schedule: CommittedSchedule,
        build: SchedulingServiceFactory,
    ) -> None:
        async with (
            AsyncSession(committed_engine) as deleting,
            AsyncSession(committed_engine) as updating,
        ):
            await build(deleting).delete_time_off(
                schedule.actor, schedule.period_id, ip_address=None
            )
            parallel = asyncio.create_task(
                build(updating).update_time_off(
                    schedule.actor,
                    schedule.period_id,
                    UpdateTimeOffRequest(reason="Conference"),
                    ip_address=None,
                )
            )
            await assert_blocked(parallel)

            await deleting.commit()
            with pytest.raises(NotFoundError):
                await parallel
