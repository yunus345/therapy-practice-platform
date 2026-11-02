from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.catalog.models import DeliveryMode, Service
from app.modules.clients.models import Client, PsychologistClient
from app.modules.scheduling.models import Appointment, AppointmentStatus, BookingChannel
from app.modules.users.models import PsychologistProfile, User, UserRole
from tests.conftest import (
    UNKNOWN_ID,
    AppointmentFactory,
    ClientFactory,
    ClientRecordFactory,
    FakeClock,
    ServiceAssigner,
    ServiceFactory,
    UserFactory,
    audit_entries,
    log_in,
)
from tests.scheduling.conftest import (
    NOW,
    Booker,
    BookerFactory,
    RuleFactory,
    TimeOffFactory,
    book,
    booking_body,
    error_code,
    history_of,
    in_status,
    local,
)

pytestmark = pytest.mark.usefixtures("fixed_time", "working_hours")


@pytest.fixture
async def record(therapist: User, make_client_record: ClientRecordFactory) -> Client:
    """Another client in the psychologist's care, who keeps the schedule busy."""
    return await make_client_record(
        therapist,
        first_name="Elif",
        last_name="Demir",
        email="elif@example.com",
        phone="+90 532 111 22 33",
    )


async def booked_appointments(session: AsyncSession) -> list[Appointment]:
    return list((await session.scalars(select(Appointment))).all())


class TestBooking:
    async def test_it_returns_the_appointment_as_the_client_sees_it(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        profile.title = "Dr."
        await db_session.commit()

        response = await book(booker, profile, service)

        assert response.status_code == 201
        body = response.json()
        assert body == {
            "id": body["id"],
            "psychologist_name": "Dr. A B",
            "psychologist_slug": profile.slug,
            "service_name": "Individual Therapy",
            "delivery_mode": "in_person",
            "starts_at": "2026-12-08T07:00:00Z",
            "ends_at": "2026-12-08T07:50:00Z",
            "timezone": "Europe/Istanbul",
            "status": "requested",
            "price_amount": "1500.00",
            "currency": "TRY",
            "rescheduled_from_id": None,
            "cancelled_at": None,
            "cancellation_reason": None,
            "created_at": body["created_at"],
        }

    async def test_the_name_leaves_out_a_missing_title(
        self, booker: Booker, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await book(booker, profile, service)

        assert response.json()["psychologist_name"] == "A B"

    async def test_it_stores_who_booked_what_and_when(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        await book(booker, profile, service)

        [appointment] = await booked_appointments(db_session)
        assert appointment.psychologist_id == therapist.id
        assert appointment.client_id == booker.record.id
        assert appointment.service_id == service.id
        assert appointment.starts_at == local(10)
        assert appointment.ends_at == local(10, 50)
        assert appointment.booking_channel is BookingChannel.ONLINE
        assert appointment.booked_by_user_id == booker.user.id

    @pytest.mark.parametrize(
        ("auto_confirm", "expected"),
        [(False, AppointmentStatus.REQUESTED), (True, AppointmentStatus.CONFIRMED)],
    )
    async def test_the_status_follows_auto_confirmation(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
        auto_confirm: bool,
        expected: AppointmentStatus,
    ) -> None:
        profile.auto_confirm_bookings = auto_confirm
        await db_session.commit()

        response = await book(booker, profile, service)

        assert response.json()["status"] == expected
        [appointment] = await booked_appointments(db_session)
        assert appointment.status is expected

    async def test_any_utc_offset_can_name_the_start(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
    ) -> None:
        body = booking_body(profile, service, local(10)) | {
            "starts_at": "2026-12-08T10:00:00+03:00"
        }

        response = await booker.api.post("/appointments", json=body)

        assert response.status_code == 201
        assert response.json()["starts_at"] == "2026-12-08T07:00:00Z"

    async def test_a_slot_from_the_public_list_can_be_booked_as_it_is(
        self, booker: Booker, profile: PsychologistProfile, service: Service
    ) -> None:
        slots = await booker.api.get(
            f"/public/psychologists/{profile.slug}/slots",
            params={"service_id": str(service.id), "from": "2026-12-08", "to": "2026-12-08"},
        )
        starts_at = slots.json()["slots"][0]["starts_at"]
        body = booking_body(profile, service, local(10)) | {"starts_at": starts_at}

        response = await booker.api.post("/appointments", json=body)

        assert response.status_code == 201

    async def test_it_writes_one_history_row(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        await book(booker, profile, service)

        [appointment] = await booked_appointments(db_session)
        [row] = await history_of(db_session, appointment.id)
        assert row.from_status is None
        assert row.to_status is AppointmentStatus.REQUESTED
        assert row.changed_by_user_id == booker.user.id
        assert row.changed_at == NOW
        assert row.reason_category is None

    async def test_it_writes_one_audit_entry_without_personal_data(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        await book(booker, profile, service)

        [appointment] = await booked_appointments(db_session)
        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_CREATED)
        assert entry.actor_user_id == booker.user.id
        assert entry.entity_type == "appointment"
        assert entry.entity_id == appointment.id
        assert entry.event_metadata == {"status": "requested", "booking_channel": "online"}


class TestPriceSnapshot:
    async def test_the_list_price_applies_without_a_custom_price(
        self, booker: Booker, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await book(booker, profile, service)

        assert response.json()["price_amount"] == "1500.00"

    @pytest.mark.parametrize(("custom", "expected"), [("2250.50", "2250.50"), ("0.00", "0.00")])
    async def test_a_custom_price_replaces_the_list_price(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
        assign_service: ServiceAssigner,
        custom: str,
        expected: str,
    ) -> None:
        priced = await make_service(duration_minutes=50, currency="USD")
        await assign_service(therapist, priced, Decimal(custom))

        response = await book(booker, profile, priced)

        assert response.json()["price_amount"] == expected
        assert response.json()["currency"] == "USD"

    async def test_later_price_changes_do_not_reach_the_appointment(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        await book(booker, profile, service)

        service.price_amount = Decimal("9999.00")
        service.currency = "EUR"
        await db_session.commit()

        listed = await booker.api.get("/me/appointments")
        assert listed.json()["items"][0]["price_amount"] == "1500.00"
        assert listed.json()["items"][0]["currency"] == "TRY"


class TestCareRelationship:
    async def relationship(
        self, session: AsyncSession, therapist: User, booker: Booker
    ) -> PsychologistClient:
        relationship = await session.scalar(
            select(PsychologistClient)
            .where(
                PsychologistClient.psychologist_id == therapist.id,
                PsychologistClient.client_id == booker.record.id,
            )
            .execution_options(populate_existing=True)
        )
        assert relationship is not None
        return relationship

    async def test_a_first_booking_opens_it(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        await book(booker, profile, service)

        relationship = await self.relationship(db_session, therapist, booker)
        assert relationship.ended_at is None

    async def test_a_booking_reopens_one_that_ended(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        db_session.add(
            PsychologistClient(
                psychologist_id=therapist.id, client_id=booker.record.id, ended_at=NOW
            )
        )
        await db_session.commit()

        response = await book(booker, profile, service)

        assert response.status_code == 201
        relationship = await self.relationship(db_session, therapist, booker)
        assert relationship.ended_at is None

    async def test_a_booking_restores_a_soft_deleted_client(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        booker.record.deleted_at = NOW
        await db_session.commit()

        response = await book(booker, profile, service)

        assert response.status_code == 201
        await db_session.refresh(booker.record)
        assert booker.record.deleted_at is None

    async def test_a_refused_booking_opens_nothing(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        response = await book(booker, profile, service, local(7))

        assert response.status_code == 409
        relationships = await db_session.scalars(select(PsychologistClient))
        assert relationships.all() == []


class TestWhoMayBook:
    async def test_an_unverified_email_is_refused_and_stores_nothing(
        self,
        make_booker: BookerFactory,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        unverified = await make_booker(email_verified_at=None)

        response = await book(unverified, profile, service)

        assert response.status_code == 403
        assert error_code(response) == "EMAIL_NOT_VERIFIED"
        assert await booked_appointments(db_session) == []

    async def test_an_account_without_a_client_record_is_not_found(
        self,
        new_client: ClientFactory,
        make_user: UserFactory,
        profile: PsychologistProfile,
        service: Service,
    ) -> None:
        client = await new_client()
        await log_in(client, (await make_user(UserRole.CLIENT)).email)

        response = await client.post(
            "/appointments", json=booking_body(profile, service, local(10))
        )

        assert response.status_code == 404


class TestWhatCanBeBooked:
    @staticmethod
    def assert_not_found(response: httpx.Response) -> None:
        assert response.status_code == 404
        assert error_code(response) == "NOT_FOUND"

    async def test_an_unknown_psychologist_is_not_found(
        self, booker: Booker, service: Service
    ) -> None:
        body = {
            "psychologist_slug": "no-such-person",
            "service_id": str(service.id),
            "starts_at": local(10).isoformat(),
        }

        self.assert_not_found(await booker.api.post("/appointments", json=body))

    async def test_a_deactivated_psychologist_is_not_found(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        therapist.is_active = False
        await db_session.commit()

        self.assert_not_found(await book(booker, profile, service))

    async def test_an_unknown_service_is_not_found(
        self, booker: Booker, profile: PsychologistProfile, service: Service
    ) -> None:
        body = booking_body(profile, service, local(10)) | {"service_id": str(UNKNOWN_ID)}

        self.assert_not_found(await booker.api.post("/appointments", json=body))

    async def test_a_service_of_a_colleague_is_not_found(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
    ) -> None:
        unassigned = await make_service()

        self.assert_not_found(await book(booker, profile, unassigned))

    async def test_a_deactivated_service_is_not_found(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        service.is_active = False
        await db_session.commit()

        self.assert_not_found(await book(booker, profile, service))


class TestFirstValidationStep:
    """A time that was never bookable is refused with `SLOT_UNAVAILABLE`."""

    @pytest.mark.parametrize(
        "starts_at",
        [
            local(8, 45),
            local(17),
            local(16, 30),
            local(10, 5),
            local(10, day=date(2026, 12, 9)),
            datetime(1950, 1, 3, 7, tzinfo=UTC),
            datetime(2150, 1, 3, 7, tzinfo=UTC),
        ],
        ids=[
            "before_hours",
            "at_closing_time",
            "session_runs_past_closing_time",
            "off_the_grid",
            "day_without_hours",
            "far_past",
            "far_future",
        ],
    )
    async def test_times_outside_the_slots_are_unavailable(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
        starts_at: datetime,
    ) -> None:
        response = await book(booker, profile, service, starts_at)

        assert response.status_code == 409
        assert error_code(response) == "SLOT_UNAVAILABLE"
        assert await booked_appointments(db_session) == []

    async def test_the_last_session_may_end_at_closing_time(
        self, booker: Booker, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await book(booker, profile, service, local(16, 0))

        assert response.status_code == 201

    @pytest.mark.parametrize(("notice_hours", "expected"), [(193, 201), (194, 409)])
    async def test_the_notice_limit_is_inclusive(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
        notice_hours: int,
        expected: int,
    ) -> None:
        profile.min_booking_notice_hours = notice_hours
        await db_session.commit()

        response = await book(booker, profile, service, local(10))

        assert response.status_code == expected

    @pytest.mark.parametrize(("starts_at", "expected"), [(local(9), 201), (local(9, 15), 409)])
    async def test_the_advance_limit_is_inclusive(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
        starts_at: datetime,
        expected: int,
    ) -> None:
        profile.max_booking_advance_days = 8
        await db_session.commit()

        response = await book(booker, profile, service, starts_at)

        assert response.status_code == expected

    async def test_time_off_makes_a_time_unavailable(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist, starts_at=local(10), ends_at=local(11))

        response = await book(booker, profile, service, local(10, 30))

        assert response.status_code == 409
        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_a_time_off_period_says_nothing_about_taken_slots(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_time_off: TimeOffFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_time_off(therapist, starts_at=local(10), ends_at=local(10, 30))
        await make_appointment(therapist, record, starts_at=local(10, 30))

        response = await book(booker, profile, service, local(10))

        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_a_rule_that_ended_no_longer_applies(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(
            therapist,
            weekday=2,
            valid_from=date(2020, 1, 1),
            valid_until=date(2026, 12, 8),
        )

        response = await book(booker, profile, service, local(10, day=date(2026, 12, 9)))

        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_a_window_pushed_past_midnight_by_dst_stays_bookable(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        clock: FakeClock,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.timezone = "America/Nuuk"
        service.duration_minutes = 15
        await db_session.commit()
        await make_rule(therapist, weekday=5, start_time=time(23, 30), end_time=time(23, 59, 59))
        clock.now = datetime(2027, 3, 20, 12, tzinfo=UTC)
        await log_in(booker.api, booker.user.email)
        # Clocks jump from 23:00 to 00:00 here, so 23:30 on Saturday is Sunday 00:30.
        starts_at = datetime(2027, 3, 28, 1, 30, tzinfo=UTC)

        slots = await booker.api.get(
            f"/public/psychologists/{profile.slug}/slots",
            params={"service_id": str(service.id), "from": "2027-03-27", "to": "2027-03-27"},
        )
        response = await book(booker, profile, service, starts_at)

        assert slots.json()["slots"][0]["starts_at"] == "2027-03-28T00:30:00-01:00"
        assert response.status_code == 201


class TestSecondValidationStep:
    """A bookable time that lost to another appointment is refused with `SLOT_TAKEN`."""

    async def test_the_same_start_is_taken(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(10))

        response = await book(booker, profile, service, local(10))

        assert response.status_code == 409
        assert error_code(response) == "SLOT_TAKEN"
        assert len(await booked_appointments(db_session)) == 1

    @pytest.mark.parametrize("starts_at", [local(9, 15), local(10, 30)])
    async def test_a_partial_overlap_is_taken(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
        starts_at: datetime,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(10))

        response = await book(booker, profile, service, starts_at)

        assert error_code(response) == "SLOT_TAKEN"

    async def test_a_session_may_start_when_another_one_ends(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(9), ends_at=local(10))

        response = await book(booker, profile, service, local(10))

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "status",
        [AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW],
    )
    async def test_a_closed_appointment_does_not_hold_its_slot(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
        status: AppointmentStatus,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(10), **in_status(status))

        response = await book(booker, profile, service, local(10))

        assert response.status_code == 201

    async def test_a_colleague_does_not_share_the_schedule(
        self,
        booker: Booker,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_user: UserFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        colleague = await make_user(UserRole.PSYCHOLOGIST)
        await make_appointment(colleague, record, starts_at=local(10))

        response = await book(booker, profile, service, local(10))

        assert response.status_code == 201

    @pytest.mark.parametrize(
        ("starts_at", "expected"),
        [
            (local(11), "SLOT_TAKEN"),
            (local(11, 15), None),
            (local(9, 30), "SLOT_TAKEN"),
            (local(9, 15), None),
        ],
        ids=["after_too_close", "after_clear", "before_too_close", "before_clear"],
    )
    async def test_the_buffer_keeps_sessions_apart(
        self,
        booker: Booker,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        starts_at: datetime,
        expected: str | None,
    ) -> None:
        profile.buffer_minutes = 15
        await db_session.commit()
        await make_appointment(therapist, record, starts_at=local(10, 20), ends_at=local(11))

        response = await book(booker, profile, service, starts_at)

        if expected is None:
            assert response.status_code == 201
        else:
            assert error_code(response) == expected


class TestRequestValidation:
    @pytest.mark.parametrize(
        "changes",
        [
            {"starts_at": "2026-12-08T10:00:00"},
            {"starts_at": "2500-01-01T10:00:00Z"},
            {"starts_at": None},
            {"service_id": "not-a-uuid"},
            {"psychologist_slug": "Not A Slug"},
            {"psychologist_slug": "x" * 101},
        ],
        ids=[
            "naive_start",
            "start_out_of_range",
            "null_start",
            "bad_service_id",
            "malformed_slug",
            "long_slug",
        ],
    )
    async def test_a_malformed_body_is_rejected(
        self,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
        changes: dict[str, Any],
    ) -> None:
        body = booking_body(profile, service, local(10)) | changes

        response = await booker.api.post("/appointments", json=body)

        assert response.status_code == 422
        assert error_code(response) == "VALIDATION_ERROR"


class TestRateLimit:
    @pytest.fixture
    def attempts(self, profile: PsychologistProfile, service: Service) -> dict[str, str]:
        return booking_body(profile, service, local(8))

    async def test_the_twenty_first_booking_of_a_session_is_limited(
        self, booker: Booker, attempts: dict[str, str]
    ) -> None:
        responses = [await booker.api.post("/appointments", json=attempts) for _ in range(21)]

        assert [response.status_code for response in responses] == [409] * 20 + [429]
        assert error_code(responses[-1]) == "RATE_LIMITED"

    async def test_another_session_keeps_its_own_count(
        self, booker: Booker, make_booker: BookerFactory, attempts: dict[str, str]
    ) -> None:
        for _ in range(20):
            await booker.api.post("/appointments", json=attempts)
        other = await make_booker()

        response = await other.api.post("/appointments", json=attempts)

        assert response.status_code == 409


class TestPublicResponsesAfterBooking:
    async def test_they_show_no_client_data_and_no_time_off_reason(
        self,
        api: httpx.AsyncClient,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_time_off: TimeOffFactory,
    ) -> None:
        reason = "Confidential reason"
        await make_time_off(therapist, starts_at=local(13), ends_at=local(14), reason=reason)
        await book(booker, profile, service, local(10))

        public_profile = await api.get(f"/public/psychologists/{profile.slug}")
        slots = await api.get(
            f"/public/psychologists/{profile.slug}/slots",
            params={"service_id": str(service.id), "from": "2026-12-08", "to": "2026-12-08"},
        )

        assert slots.status_code == 200
        for text in (public_profile.text, slots.text):
            assert booker.record.first_name not in text
            assert booker.record.last_name not in text
            assert booker.record.phone not in text
            assert booker.user.email not in text
            assert str(booker.record.id) not in text
            assert reason not in text

    async def test_the_booked_time_simply_disappears_from_the_slots(
        self,
        api: httpx.AsyncClient,
        booker: Booker,
        profile: PsychologistProfile,
        service: Service,
    ) -> None:
        await book(booker, profile, service, local(10))

        slots = await api.get(
            f"/public/psychologists/{profile.slug}/slots",
            params={"service_id": str(service.id), "from": "2026-12-08", "to": "2026-12-08"},
        )

        starts = [slot["starts_at"][11:16] for slot in slots.json()["slots"]]
        assert "10:00" not in starts
        assert "10:30" not in starts
        assert "11:00" in starts


async def test_delivery_mode_is_shown_to_the_client(
    booker: Booker,
    therapist: User,
    profile: PsychologistProfile,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    online = await make_service(delivery_mode=DeliveryMode.ONLINE, duration_minutes=50)
    await assign_service(therapist, online)

    response = await book(booker, profile, online)

    assert response.json()["delivery_mode"] == "online"


async def test_a_new_appointment_has_no_cancellation_details(
    booker: Booker,
    profile: PsychologistProfile,
    service: Service,
    db_session: AsyncSession,
) -> None:
    await book(booker, profile, service)

    [appointment] = await booked_appointments(db_session)
    assert appointment.cancelled_at is None
    assert appointment.cancelled_by_user_id is None
    assert appointment.cancellation_reason is None
    assert appointment.rescheduled_from_id is None
