"""A client's own cancellations and reschedules."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.catalog.models import PsychologistService, Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import (
    Appointment,
    AppointmentStatus,
    BookingChannel,
    CancellationReason,
)
from app.modules.users.models import PsychologistProfile, User, UserRole
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
from tests.scheduling.conftest import (
    NOW,
    Booker,
    BookerFactory,
    TimeOffFactory,
    book,
    error_code,
    history_of,
    in_status,
    load_appointment,
    local,
)

pytestmark = pytest.mark.usefixtures("fixed_time", "working_hours")

TARGET = local(12)

both_changes = pytest.mark.parametrize("action", ["cancel", "reschedule"])


@pytest.fixture
async def booked(booker: Booker, profile: PsychologistProfile, service: Service) -> uuid.UUID:
    """A 50 minute session at 10:00 on Tuesday, booked through the API."""
    response = await book(booker, profile, service)
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


@pytest.fixture
async def record(therapist: User, make_client_record: ClientRecordFactory) -> Client:
    """Another client in the psychologist's care, who keeps the schedule busy."""
    return await make_client_record(therapist)


async def cancel(booker: Booker, appointment_id: uuid.UUID) -> httpx.Response:
    return await booker.api.post(f"/appointments/{appointment_id}/cancel")


async def reschedule(
    booker: Booker, appointment_id: uuid.UUID, starts_at: datetime = TARGET
) -> httpx.Response:
    return await booker.api.post(
        f"/appointments/{appointment_id}/reschedule", json={"starts_at": starts_at.isoformat()}
    )


async def change(booker: Booker, appointment_id: uuid.UUID, action: str) -> httpx.Response:
    if action == "cancel":
        return await cancel(booker, appointment_id)
    return await reschedule(booker, appointment_id)


async def count_appointments(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Appointment)) or 0


async def assert_still_active(
    session: AsyncSession,
    appointment_id: uuid.UUID,
    status: AppointmentStatus = AppointmentStatus.REQUESTED,
) -> None:
    appointment = await load_appointment(session, appointment_id)
    assert appointment.status is status
    assert appointment.cancelled_at is None
    assert appointment.cancellation_reason is None


class TestChangeRules:
    """Cancelling and rescheduling share who may do it, and until when."""

    @both_changes
    async def test_an_appointment_of_another_client_is_not_found_and_stays_as_it_is(
        self,
        make_booker: BookerFactory,
        booked: uuid.UUID,
        db_session: AsyncSession,
        action: str,
    ) -> None:
        stranger = await make_booker()

        response = await change(stranger, booked, action)

        assert response.status_code == 404
        assert error_code(response) == "NOT_FOUND"
        await assert_still_active(db_session, booked)
        assert len(await history_of(db_session, booked)) == 1

    @both_changes
    async def test_an_unknown_appointment_is_not_found(self, booker: Booker, action: str) -> None:
        response = await change(booker, UNKNOWN_ID, action)

        assert response.status_code == 404

    @both_changes
    async def test_an_account_without_a_client_record_is_not_found(
        self,
        new_client: ClientFactory,
        make_user: UserFactory,
        booked: uuid.UUID,
        action: str,
    ) -> None:
        user = await make_user(UserRole.CLIENT)
        client = await new_client()
        await log_in(client, user.email)

        response = await change(Booker(user, Client(), client), booked, action)

        assert response.status_code == 404

    @both_changes
    @pytest.mark.parametrize(
        "status",
        [AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW],
    )
    async def test_a_final_status_cannot_change(
        self,
        booker: Booker,
        therapist: User,
        make_appointment: AppointmentFactory,
        action: str,
        status: AppointmentStatus,
    ) -> None:
        appointment = await make_appointment(
            therapist, booker.record, starts_at=local(10), **in_status(status)
        )

        response = await change(booker, appointment.id, action)

        assert response.status_code == 409
        assert error_code(response) == "INVALID_STATUS_TRANSITION"

    @both_changes
    async def test_a_final_status_is_reported_ahead_of_a_closed_window(
        self,
        booker: Booker,
        therapist: User,
        make_appointment: AppointmentFactory,
        action: str,
    ) -> None:
        appointment = await make_appointment(
            therapist,
            booker.record,
            starts_at=NOW - timedelta(days=2),
            **in_status(AppointmentStatus.COMPLETED),
        )

        response = await change(booker, appointment.id, action)

        assert error_code(response) == "INVALID_STATUS_TRANSITION"

    @both_changes
    async def test_the_window_is_open_at_its_exact_limit(
        self,
        booker: Booker,
        therapist: User,
        make_appointment: AppointmentFactory,
        action: str,
    ) -> None:
        appointment = await make_appointment(
            therapist, booker.record, starts_at=NOW + timedelta(hours=24)
        )

        response = await change(booker, appointment.id, action)

        assert response.status_code in {200, 201}

    @both_changes
    async def test_the_window_closes_one_second_later(
        self,
        booker: Booker,
        therapist: User,
        clock: FakeClock,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        action: str,
    ) -> None:
        appointment = await make_appointment(
            therapist, booker.record, starts_at=NOW + timedelta(hours=24)
        )
        clock.advance(seconds=1)

        response = await change(booker, appointment.id, action)

        assert response.status_code == 409
        assert response.json()["error"] == {
            "code": "CANCELLATION_WINDOW_CLOSED",
            "message": "It is too late to change this appointment.",
            "details": {"cancellation_window_hours": 24},
        }
        await assert_still_active(db_session, appointment.id, AppointmentStatus.CONFIRMED)

    @both_changes
    async def test_the_window_of_the_psychologist_applies(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        action: str,
    ) -> None:
        profile.cancellation_window_hours = 48
        await db_session.commit()
        appointment = await make_appointment(
            therapist, booker.record, starts_at=NOW + timedelta(hours=47)
        )

        response = await change(booker, appointment.id, action)

        assert response.json()["error"]["details"] == {"cancellation_window_hours": 48}

    @pytest.mark.parametrize(
        ("late_by", "expected"), [(timedelta(0), 200), (timedelta(seconds=1), 409)]
    )
    async def test_without_a_window_the_start_is_the_limit(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        late_by: timedelta,
        expected: int,
    ) -> None:
        profile.cancellation_window_hours = 0
        await db_session.commit()
        appointment = await make_appointment(therapist, booker.record, starts_at=NOW - late_by)

        response = await cancel(booker, appointment.id)

        assert response.status_code == expected


class TestCancel:
    async def test_it_returns_the_updated_appointment(
        self, booker: Booker, booked: uuid.UUID
    ) -> None:
        response = await cancel(booker, booked)

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(booked)
        assert body["status"] == "cancelled"
        assert body["cancelled_at"] == "2026-11-30T06:00:00Z"
        assert body["cancellation_reason"] == "client_request"

    async def test_it_records_who_cancelled_and_why(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        await cancel(booker, booked)

        appointment = await load_appointment(db_session, booked)
        assert appointment.status is AppointmentStatus.CANCELLED
        assert appointment.cancelled_at == NOW
        assert appointment.cancelled_by_user_id == booker.user.id
        assert appointment.cancellation_reason is CancellationReason.CLIENT_REQUEST

    async def test_it_adds_one_history_row(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        await cancel(booker, booked)

        [_, row] = await history_of(db_session, booked)
        assert row.from_status is AppointmentStatus.REQUESTED
        assert row.to_status is AppointmentStatus.CANCELLED
        assert row.changed_by_user_id == booker.user.id
        assert row.changed_at == NOW
        assert row.reason_category is CancellationReason.CLIENT_REQUEST

    async def test_it_adds_one_audit_entry_without_personal_data(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        await cancel(booker, booked)

        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_STATUS_CHANGED)
        assert entry.actor_user_id == booker.user.id
        assert entry.entity_type == "appointment"
        assert entry.entity_id == booked
        assert entry.event_metadata == {
            "from_status": "requested",
            "to_status": "cancelled",
            "reason_category": "client_request",
        }

    async def test_a_second_cancellation_is_refused_and_records_nothing(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        await cancel(booker, booked)

        response = await cancel(booker, booked)

        assert error_code(response) == "INVALID_STATUS_TRANSITION"
        assert len(await history_of(db_session, booked)) == 2

    async def test_a_confirmed_appointment_can_be_cancelled(
        self, booker: Booker, therapist: User, make_appointment: AppointmentFactory
    ) -> None:
        appointment = await make_appointment(therapist, booker.record, starts_at=local(10))

        response = await cancel(booker, appointment.id)

        assert response.json()["status"] == "cancelled"

    async def test_the_slot_becomes_free_again(
        self,
        booker: Booker,
        make_booker: BookerFactory,
        profile: PsychologistProfile,
        service: Service,
        booked: uuid.UUID,
    ) -> None:
        await cancel(booker, booked)
        other = await make_booker()

        response = await book(other, profile, service)

        assert response.status_code == 201


class TestReschedule:
    async def test_it_returns_the_new_appointment_with_201(
        self, booker: Booker, booked: uuid.UUID
    ) -> None:
        response = await reschedule(booker, booked)

        assert response.status_code == 201
        body = response.json()
        assert body["id"] != str(booked)
        assert body["rescheduled_from_id"] == str(booked)
        assert body["starts_at"] == "2026-12-08T09:00:00Z"
        assert body["ends_at"] == "2026-12-08T09:50:00Z"
        assert body["status"] == "requested"

    async def test_it_cancels_the_old_appointment_as_rescheduled(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        await reschedule(booker, booked)

        old = await load_appointment(db_session, booked)
        assert old.status is AppointmentStatus.CANCELLED
        assert old.cancelled_at == NOW
        assert old.cancelled_by_user_id == booker.user.id
        assert old.cancellation_reason is CancellationReason.RESCHEDULED

    async def test_it_books_the_new_one_as_an_online_booking_by_the_client(
        self,
        booker: Booker,
        therapist: User,
        service: Service,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        response = await reschedule(booker, booked)

        new = await load_appointment(db_session, uuid.UUID(response.json()["id"]))
        assert new.rescheduled_from_id == booked
        assert new.psychologist_id == therapist.id
        assert new.client_id == booker.record.id
        assert new.service_id == service.id
        assert new.booking_channel is BookingChannel.ONLINE
        assert new.booked_by_user_id == booker.user.id
        assert new.cancelled_at is None

    async def test_it_writes_a_history_row_for_each_appointment(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        response = await reschedule(booker, booked)

        [_, old_row] = await history_of(db_session, booked)
        assert old_row.from_status is AppointmentStatus.REQUESTED
        assert old_row.to_status is AppointmentStatus.CANCELLED
        assert old_row.reason_category is CancellationReason.RESCHEDULED
        assert old_row.changed_by_user_id == booker.user.id
        [new_row] = await history_of(db_session, uuid.UUID(response.json()["id"]))
        assert new_row.from_status is None
        assert new_row.to_status is AppointmentStatus.REQUESTED
        assert new_row.reason_category is CancellationReason.RESCHEDULED
        assert new_row.changed_by_user_id == booker.user.id

    async def test_it_writes_one_audit_entry_for_the_whole_move(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        response = await reschedule(booker, booked)

        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_RESCHEDULED)
        assert entry.actor_user_id == booker.user.id
        assert entry.entity_type == "appointment"
        assert entry.entity_id == booked
        assert entry.event_metadata == {
            "from_status": "requested",
            "to_status": "cancelled",
            "reason_category": "rescheduled",
            "new_appointment_id": response.json()["id"],
        }
        assert await audit_entries(db_session, AuditAction.APPOINTMENT_STATUS_CHANGED) == []
        assert len(await audit_entries(db_session, AuditAction.APPOINTMENT_CREATED)) == 1

    @pytest.mark.parametrize(
        ("auto_confirm", "expected"),
        [(False, AppointmentStatus.REQUESTED), (True, AppointmentStatus.CONFIRMED)],
    )
    async def test_the_new_status_follows_auto_confirmation(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        auto_confirm: bool,
        expected: AppointmentStatus,
    ) -> None:
        profile.auto_confirm_bookings = auto_confirm
        await db_session.commit()
        appointment = await make_appointment(therapist, booker.record, starts_at=local(10))

        response = await reschedule(booker, appointment.id)

        assert response.json()["status"] == expected

    async def test_an_unverified_email_is_refused_and_changes_nothing(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        booker.user.email_verified_at = None
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert response.status_code == 403
        assert error_code(response) == "EMAIL_NOT_VERIFIED"
        await assert_still_active(db_session, booked)

    async def test_an_unverified_email_may_still_cancel(
        self, booker: Booker, booked: uuid.UUID, db_session: AsyncSession
    ) -> None:
        booker.user.email_verified_at = None
        await db_session.commit()

        response = await cancel(booker, booked)

        assert response.status_code == 200

    async def test_a_moved_appointment_cannot_be_moved_again_but_its_successor_can(
        self, booker: Booker, booked: uuid.UUID
    ) -> None:
        moved = await reschedule(booker, booked)

        again = await reschedule(booker, booked, local(14))
        onward = await reschedule(booker, uuid.UUID(moved.json()["id"]), local(14))

        assert error_code(again) == "INVALID_STATUS_TRANSITION"
        assert onward.status_code == 201
        assert onward.json()["rescheduled_from_id"] == moved.json()["id"]


class TestWhatAMoveKeeps:
    async def test_the_price_stays_after_the_list_price_changed(
        self,
        booker: Booker,
        service: Service,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        service.price_amount = Decimal("9999.00")
        service.currency = "EUR"
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert response.json()["price_amount"] == "1500.00"
        assert response.json()["currency"] == "TRY"

    async def test_the_duration_stays_after_the_service_changed(
        self,
        booker: Booker,
        service: Service,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        service.duration_minutes = 120
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert response.json()["ends_at"] == "2026-12-08T09:50:00Z"

    async def test_it_works_after_the_service_was_deactivated(
        self,
        booker: Booker,
        service: Service,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        service.is_active = False
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert response.status_code == 201
        assert response.json()["service_name"] == "Individual Therapy"
        assert response.json()["price_amount"] == "1500.00"

    async def test_it_works_after_the_service_was_unassigned(
        self,
        booker: Booker,
        service: Service,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        await db_session.execute(
            delete(PsychologistService).where(PsychologistService.service_id == service.id)
        )
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert response.status_code == 201


class TestWhereAMoveCanGo:
    async def test_a_session_may_move_inside_its_own_old_range(
        self, booker: Booker, booked: uuid.UUID
    ) -> None:
        response = await reschedule(booker, booked, local(10, 15))

        assert response.status_code == 201
        assert response.json()["starts_at"] == "2026-12-08T07:15:00Z"

    async def test_its_own_buffer_does_not_stand_in_the_way(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.buffer_minutes = 30
        await db_session.commit()
        appointment = await make_appointment(therapist, booker.record, starts_at=local(10))

        response = await reschedule(booker, appointment.id, local(11))

        assert response.status_code == 201

    async def test_the_time_of_another_appointment_is_taken(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        booked: uuid.UUID,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(therapist, record, starts_at=TARGET)

        response = await reschedule(booker, booked)

        assert response.status_code == 409
        assert error_code(response) == "SLOT_TAKEN"

    async def test_the_buffer_of_another_appointment_counts_as_taken(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        booked: uuid.UUID,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.buffer_minutes = 30
        await db_session.commit()
        await make_appointment(therapist, record, starts_at=TARGET + timedelta(minutes=60))

        response = await reschedule(booker, booked)

        assert error_code(response) == "SLOT_TAKEN"

    @pytest.mark.parametrize(
        "starts_at", [local(8), local(10, 5)], ids=["outside_hours", "off_the_grid"]
    )
    async def test_a_time_that_is_no_slot_is_unavailable(
        self, booker: Booker, booked: uuid.UUID, starts_at: datetime
    ) -> None:
        response = await reschedule(booker, booked, starts_at)

        assert response.status_code == 409
        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_time_off_is_unavailable(
        self,
        booker: Booker,
        therapist: User,
        booked: uuid.UUID,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist, starts_at=TARGET, ends_at=TARGET + timedelta(hours=1))

        response = await reschedule(booker, booked)

        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_inside_the_notice_period_is_unavailable(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        profile.min_booking_notice_hours = 500
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_a_deactivated_psychologist_takes_no_new_session(
        self,
        booker: Booker,
        therapist: User,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        therapist.is_active = False
        await db_session.commit()

        response = await reschedule(booker, booked)

        assert error_code(response) == "SLOT_UNAVAILABLE"
        await assert_still_active(db_session, booked)

    async def test_a_deactivated_psychologist_does_not_stop_a_cancellation(
        self,
        booker: Booker,
        therapist: User,
        booked: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        therapist.is_active = False
        await db_session.commit()

        response = await cancel(booker, booked)

        assert response.status_code == 200

    @pytest.mark.parametrize(
        "body",
        [{}, {"starts_at": "2026-12-08T10:00:00"}, {"starts_at": None}],
        ids=["missing", "naive", "null"],
    )
    async def test_a_malformed_body_is_rejected(
        self, booker: Booker, booked: uuid.UUID, body: dict[str, Any]
    ) -> None:
        response = await booker.api.post(f"/appointments/{booked}/reschedule", json=body)

        assert response.status_code == 422


class TestRescheduleAtomicity:
    async def assert_nothing_happened(self, session: AsyncSession, old: uuid.UUID) -> None:
        await assert_still_active(session, old)
        assert len(await history_of(session, old)) == 1
        assert await count_appointments(session) == 2
        assert await audit_entries(session, AuditAction.APPOINTMENT_RESCHEDULED) == []
        assert await audit_entries(session, AuditAction.APPOINTMENT_STATUS_CHANGED) == []

    async def test_a_taken_target_leaves_the_old_appointment_active(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        booked: uuid.UUID,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        await make_appointment(therapist, record, starts_at=TARGET)

        response = await reschedule(booker, booked)

        assert response.status_code == 409
        await self.assert_nothing_happened(db_session, booked)

    async def test_an_unavailable_target_leaves_the_old_appointment_active(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        booked: uuid.UUID,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(14))

        response = await reschedule(booker, booked, local(8))

        assert response.status_code == 409
        await self.assert_nothing_happened(db_session, booked)

    async def test_the_old_slot_stays_held_after_a_failed_move(
        self,
        booker: Booker,
        make_booker: BookerFactory,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        booked: uuid.UUID,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(therapist, record, starts_at=TARGET)
        await reschedule(booker, booked)
        other = await make_booker()

        response = await book(other, profile, service)

        assert error_code(response) == "SLOT_TAKEN"


async def test_a_reschedule_leaves_the_care_relationship_active(
    booker: Booker,
    booked: uuid.UUID,
    therapist_api: httpx.AsyncClient,
) -> None:
    await reschedule(booker, booked)

    response = await therapist_api.get(f"/staff/clients/{booker.record.id}")

    assert response.status_code == 200


async def test_a_staff_booking_can_be_cancelled_by_its_client(
    booker: Booker,
    therapist: User,
    make_appointment: AppointmentFactory,
) -> None:
    appointment = await make_appointment(
        therapist,
        booker.record,
        starts_at=local(10),
        booking_channel=BookingChannel.STAFF,
        booked_by_user_id=therapist.id,
    )

    response = await cancel(booker, appointment.id)

    assert response.status_code == 200
