import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.audit.models import AuditLog
from app.modules.catalog.models import DeliveryMode, PsychologistService, Service
from app.modules.clients.models import Client, PsychologistClient
from app.modules.consent.models import Consent, ConsentType
from app.modules.scheduling.models import (
    NO_OVERLAP_CONSTRAINT,
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    AvailabilityRule,
    BookingChannel,
    CancellationReason,
    TimeOff,
)
from app.modules.users.models import PsychologistProfile, User, UserRole

START = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


async def add[Row](session: AsyncSession, row: Row) -> Row:
    session.add(row)
    await session.flush()
    return row


async def assert_rejected(session: AsyncSession, row: Any, constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():
            await add(session, row)


async def make_user(session: AsyncSession, role: UserRole = UserRole.PSYCHOLOGIST) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", role=role)
    return await add(session, user)


async def make_psychologist(session: AsyncSession) -> PsychologistProfile:
    user = await make_user(session)
    profile = PsychologistProfile(
        user_id=user.id, first_name="A", last_name="B", slug=f"p-{user.id.hex}"
    )
    return await add(session, profile)


async def make_client(session: AsyncSession) -> Client:
    creator = await make_user(session, UserRole.ADMIN)
    client = Client(
        first_name="C", last_name="D", phone="+905320000000", created_by_user_id=creator.id
    )
    return await add(session, client)


async def make_service(session: AsyncSession, **overrides: Any) -> Service:
    values: dict[str, Any] = {
        "name": "Individual",
        "duration_minutes": 50,
        "price_amount": Decimal("1500.00"),
        "delivery_mode": DeliveryMode.ONLINE,
    }
    return await add(session, Service(**(values | overrides)))


class AppointmentFactory:
    def __init__(self, psychologist: PsychologistProfile, client: Client, service: Service) -> None:
        self.psychologist_id = psychologist.user_id
        self.client = client
        self.service = service

    def build(
        self, starts_at: datetime = START, minutes: int = 50, **overrides: Any
    ) -> Appointment:
        values: dict[str, Any] = {
            "psychologist_id": self.psychologist_id,
            "client_id": self.client.id,
            "service_id": self.service.id,
            "starts_at": starts_at,
            "ends_at": starts_at + timedelta(minutes=minutes),
            "status": AppointmentStatus.CONFIRMED,
            "price_amount": Decimal("1500.00"),
            "currency": "TRY",
            "booking_channel": BookingChannel.ONLINE,
            "booked_by_user_id": self.client.created_by_user_id,
        }
        return Appointment(**(values | overrides))


@pytest.fixture
async def appointments(db_session: AsyncSession) -> AppointmentFactory:
    return AppointmentFactory(
        await make_psychologist(db_session),
        await make_client(db_session),
        await make_service(db_session),
    )


@pytest.mark.parametrize("first", [AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
@pytest.mark.parametrize("second", [AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
async def test_active_appointments_of_a_psychologist_cannot_overlap(
    db_session: AsyncSession,
    appointments: AppointmentFactory,
    first: AppointmentStatus,
    second: AppointmentStatus,
) -> None:
    await add(db_session, appointments.build(status=first))

    await assert_rejected(
        db_session,
        appointments.build(START + timedelta(minutes=30), status=second),
        NO_OVERLAP_CONSTRAINT,
    )


async def test_adjacent_appointments_do_not_overlap(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    await add(db_session, appointments.build())

    await add(db_session, appointments.build(START + timedelta(minutes=50)))


@pytest.mark.parametrize(
    ("status", "extra"),
    [
        (AppointmentStatus.COMPLETED, {}),
        (AppointmentStatus.NO_SHOW, {}),
        (
            AppointmentStatus.CANCELLED,
            {"cancelled_at": START, "cancellation_reason": CancellationReason.OTHER},
        ),
    ],
)
async def test_inactive_appointments_do_not_block_the_slot(
    db_session: AsyncSession,
    appointments: AppointmentFactory,
    status: AppointmentStatus,
    extra: dict[str, Any],
) -> None:
    await add(db_session, appointments.build(status=status, **extra))

    await add(db_session, appointments.build())


async def test_different_psychologists_may_overlap(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    await add(db_session, appointments.build())
    other = await make_psychologist(db_session)

    await add(db_session, appointments.build(psychologist_id=other.user_id))


async def test_status_update_into_overlap_is_rejected(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    await add(db_session, appointments.build())
    cancelled = await add(
        db_session,
        appointments.build(
            status=AppointmentStatus.CANCELLED,
            cancelled_at=START,
            cancellation_reason=CancellationReason.OTHER,
        ),
    )

    with pytest.raises(IntegrityError, match=NO_OVERLAP_CONSTRAINT):
        async with db_session.begin_nested():
            cancelled.status = AppointmentStatus.CONFIRMED
            cancelled.cancelled_at = None
            cancelled.cancellation_reason = None
            await db_session.flush()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"minutes": 0}, "ck_appointments_start_before_end"),
        ({"price_amount": Decimal("-1")}, "ck_appointments_price_not_negative"),
        ({"currency": "try"}, "ck_appointments_currency_format"),
        ({"cancelled_at": START}, "ck_appointments_cancelled_at_matches_status"),
        (
            {"cancellation_reason": CancellationReason.OTHER},
            "ck_appointments_cancellation_reason_matches_status",
        ),
        (
            {
                "status": AppointmentStatus.CANCELLED,
                "cancellation_reason": CancellationReason.OTHER,
            },
            "ck_appointments_cancelled_at_matches_status",
        ),
        (
            {"status": AppointmentStatus.CANCELLED, "cancelled_at": START},
            "ck_appointments_cancellation_reason_matches_status",
        ),
    ],
)
async def test_appointment_checks(
    db_session: AsyncSession,
    appointments: AppointmentFactory,
    overrides: dict[str, Any],
    constraint: str,
) -> None:
    await assert_rejected(db_session, appointments.build(**overrides), constraint)


async def test_appointment_can_be_rescheduled_only_once(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    original = await add(
        db_session,
        appointments.build(
            status=AppointmentStatus.CANCELLED,
            cancelled_at=START,
            cancellation_reason=CancellationReason.RESCHEDULED,
        ),
    )
    await add(
        db_session,
        appointments.build(START + timedelta(days=1), rescheduled_from_id=original.id),
    )

    await assert_rejected(
        db_session,
        appointments.build(START + timedelta(days=2), rescheduled_from_id=original.id),
        "uq_appointments_rescheduled_from_id",
    )


async def test_user_email_is_case_insensitively_unique(db_session: AsyncSession) -> None:
    await add(db_session, User(email="Same@Example.com", role=UserRole.CLIENT))

    await assert_rejected(
        db_session, User(email="same@example.com", role=UserRole.CLIENT), "uq_users_email"
    )


async def test_totp_cannot_be_enabled_without_a_secret(db_session: AsyncSession) -> None:
    await assert_rejected(
        db_session,
        User(email="a@example.com", role=UserRole.ADMIN, totp_enabled=True),
        "ck_users_totp_enabled_requires_secret",
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"slug": "Not Valid"}, "ck_psychologist_profiles_slug_format"),
        ({"min_booking_notice_hours": -1}, "ck_psychologist_profiles_notice_not_negative"),
        ({"max_booking_advance_days": 0}, "ck_psychologist_profiles_advance_positive"),
        ({"cancellation_window_hours": -1}, "ck_psychologist_profiles_cancellation_not_negative"),
        ({"buffer_minutes": 121}, "ck_psychologist_profiles_buffer_range"),
    ],
)
async def test_profile_checks(
    db_session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    user = await make_user(db_session)
    values: dict[str, Any] = {
        "user_id": user.id,
        "first_name": "A",
        "last_name": "B",
        "slug": "valid-slug",
    }

    await assert_rejected(db_session, PsychologistProfile(**(values | overrides)), constraint)


async def test_profile_defaults(db_session: AsyncSession) -> None:
    profile = await make_psychologist(db_session)

    assert profile.timezone == "Europe/Istanbul"
    assert profile.buffer_minutes == 0
    assert profile.auto_confirm_bookings is False


async def test_care_relationship_pair_is_unique(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    pair = {"psychologist_id": appointments.psychologist_id, "client_id": appointments.client.id}
    await add(db_session, PsychologistClient(**pair))

    await assert_rejected(
        db_session,
        PsychologistClient(**pair),
        "uq_psychologist_clients_psychologist_id_client_id",
    )


async def test_service_assignment_pair_is_unique(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    pair = {"psychologist_id": appointments.psychologist_id, "service_id": appointments.service.id}
    await add(db_session, PsychologistService(**pair))

    await assert_rejected(
        db_session,
        PsychologistService(**pair),
        "uq_psychologist_services_psychologist_id_service_id",
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"duration_minutes": 0}, "ck_services_duration_positive"),
        ({"price_amount": Decimal("-0.01")}, "ck_services_price_not_negative"),
        ({"currency": "TL"}, "ck_services_currency_format"),
    ],
)
async def test_service_checks(
    db_session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with db_session.begin_nested():
            await make_service(db_session, **overrides)


async def test_money_keeps_two_decimals_and_default_currency(db_session: AsyncSession) -> None:
    service_id = (await make_service(db_session, price_amount=Decimal("1234.56"))).id
    db_session.expire_all()

    stored = await db_session.scalar(select(Service).where(Service.id == service_id))

    assert stored is not None
    assert stored.price_amount == Decimal("1234.56")
    assert stored.currency == "TRY"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"weekday": 7}, "ck_availability_rules_weekday_range"),
        ({"end_time": time(9, 0)}, "ck_availability_rules_start_before_end"),
        ({"valid_until": date(2025, 12, 31)}, "ck_availability_rules_valid_range"),
    ],
)
async def test_availability_checks(
    db_session: AsyncSession,
    appointments: AppointmentFactory,
    overrides: dict[str, Any],
    constraint: str,
) -> None:
    values: dict[str, Any] = {
        "psychologist_id": appointments.psychologist_id,
        "weekday": 0,
        "start_time": time(9, 0),
        "end_time": time(17, 0),
        "valid_from": date(2026, 1, 1),
    }

    await assert_rejected(db_session, AvailabilityRule(**(values | overrides)), constraint)


async def test_time_off_must_end_after_it_starts(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    await assert_rejected(
        db_session,
        TimeOff(psychologist_id=appointments.psychologist_id, starts_at=START, ends_at=START),
        "ck_time_off_start_before_end",
    )


async def test_only_one_active_consent_per_type_and_version(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    values: dict[str, Any] = {
        "client_id": appointments.client.id,
        "consent_type": ConsentType.KVKK_DISCLOSURE,
        "version": "1.0",
    }
    first = await add(db_session, Consent(**values))

    await assert_rejected(db_session, Consent(**values), "uq_consents_active")

    first.revoked_at = datetime.now(UTC)
    await db_session.flush()
    await add(db_session, Consent(**values))


async def test_consent_cannot_be_revoked_before_it_was_granted(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    await assert_rejected(
        db_session,
        Consent(
            client_id=appointments.client.id,
            consent_type=ConsentType.ANALYTICS,
            version="1.0",
            granted_at=START,
            revoked_at=START - timedelta(days=1),
        ),
        "ck_consents_revoked_after_granted",
    )


async def test_updated_at_is_set_by_the_trigger_and_read_back(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    stale = datetime(2000, 1, 1, tzinfo=UTC)
    set_committed_value(user, "updated_at", stale)

    user.is_active = False
    await db_session.flush()

    assert user.updated_at > stale


async def test_trigger_overrides_a_written_updated_at(db_session: AsyncSession) -> None:
    user = await make_user(db_session)

    stored = await db_session.scalar(
        text("UPDATE users SET updated_at = '2000-01-01' WHERE id = :id RETURNING updated_at"),
        {"id": user.id},
    )

    assert stored.year > 2000


async def test_timestamps_are_stored_with_timezone(db_session: AsyncSession) -> None:
    user = await make_user(db_session)

    assert user.created_at.tzinfo is not None


async def test_audit_log_accepts_inserts_with_default_metadata(db_session: AsyncSession) -> None:
    row = await add(db_session, AuditLog(action="auth.login_failed"))

    assert row.event_metadata == {}
    assert row.occurred_at.tzinfo is not None


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_logs SET action = 'changed'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
        "UPDATE appointment_status_history SET reason_category = 'other'",
        "DELETE FROM appointment_status_history",
        "TRUNCATE appointment_status_history",
    ],
)
async def test_application_role_cannot_change_append_only_tables(
    db_session: AsyncSession, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db_session.begin_nested():
            await db_session.execute(text(statement))


async def test_application_role_can_write_status_history(
    db_session: AsyncSession, appointments: AppointmentFactory
) -> None:
    appointment = await add(db_session, appointments.build())

    await add(
        db_session,
        AppointmentStatusHistory(
            appointment_id=appointment.id, to_status=AppointmentStatus.CONFIRMED
        ),
    )


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE users CASCADE",
        "ALTER TABLE audit_logs DISABLE TRIGGER ALL",
        "CREATE TABLE stolen (id int)",
        "SELECT * FROM alembic_version",
    ],
)
async def test_application_role_has_no_schema_privileges(
    db_session: AsyncSession, statement: str
) -> None:
    with pytest.raises(DBAPIError, match=r"must be owner|permission denied"):
        async with db_session.begin_nested():
            await db_session.execute(text(statement))
