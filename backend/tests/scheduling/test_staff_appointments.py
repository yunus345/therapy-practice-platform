import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.catalog.models import Service
from app.modules.clients.models import Client, PsychologistClient
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
    ServiceAssigner,
    ServiceFactory,
    UserFactory,
    audit_entries,
)
from tests.scheduling.conftest import (
    NOW,
    SESSION_START,
    TimeOffFactory,
    error_code,
    history_of,
    ids_of,
    in_status,
    listed_ids,
    load_appointment,
    local,
    logged_in,
)

pytestmark = pytest.mark.usefixtures("fixed_time")

CLIENT_NAME = {"first_name": "Zeynep", "last_name": "Yilmaz"}
MOVE_TARGET = local(12)


@pytest.fixture
async def record(therapist: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(therapist, **CLIENT_NAME)


@pytest.fixture
async def colleague(make_user: UserFactory) -> User:
    return await make_user(UserRole.PSYCHOLOGIST)


@pytest.fixture
async def colleague_record(colleague: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(colleague)


@pytest.fixture
async def admin_api(new_client: ClientFactory, make_user: UserFactory) -> httpx.AsyncClient:
    return await logged_in(new_client, make_user, UserRole.ADMIN)


def staff_booking(record: Client, service: Service, starts_at: datetime) -> dict[str, str]:
    return {
        "client_id": str(record.id),
        "service_id": str(service.id),
        "starts_at": starts_at.isoformat(),
    }


class TestList:
    async def test_it_shows_every_staff_field(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        make_appointment: AppointmentFactory,
    ) -> None:
        cancelled = await make_appointment(
            therapist,
            record,
            service_id=service.id,
            starts_at=local(10),
            ends_at=local(10, 50),
            cancelled_by_user_id=therapist.id,
            **in_status(AppointmentStatus.CANCELLED),
        )

        response = await therapist_api.get("/staff/appointments")

        [item] = response.json()["items"]
        assert item == {
            "id": str(cancelled.id),
            "psychologist_id": str(therapist.id),
            "client_id": str(record.id),
            "client_first_name": "Zeynep",
            "client_last_name": "Yilmaz",
            "service_id": str(service.id),
            "service_name": "Individual Therapy",
            "delivery_mode": "in_person",
            "starts_at": "2026-12-08T07:00:00Z",
            "ends_at": "2026-12-08T07:50:00Z",
            "status": "cancelled",
            "price_amount": "1500.00",
            "currency": "TRY",
            "booking_channel": "staff",
            "booked_by_user_id": str(therapist.id),
            "rescheduled_from_id": None,
            "cancelled_at": "2026-11-30T06:00:00Z",
            "cancelled_by_user_id": str(therapist.id),
            "cancellation_reason": "other",
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }

    async def test_a_psychologist_sees_only_their_own(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        colleague: User,
        record: Client,
        colleague_record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        own = await make_appointment(therapist, record)
        await make_appointment(colleague, colleague_record)

        response = await therapist_api.get("/staff/appointments")

        assert listed_ids(response) == ids_of(own)
        assert response.json()["total"] == 1

    async def test_a_psychologist_may_name_themselves(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        own = await make_appointment(therapist, record)

        response = await therapist_api.get(
            "/staff/appointments", params={"psychologist_id": str(therapist.id)}
        )

        assert listed_ids(response) == ids_of(own)

    async def test_naming_another_psychologist_is_not_found(
        self, therapist_api: httpx.AsyncClient, colleague: User
    ) -> None:
        response = await therapist_api.get(
            "/staff/appointments", params={"psychologist_id": str(colleague.id)}
        )

        assert response.status_code == 404

    async def test_an_ended_care_relationship_keeps_its_appointments_listed(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(
            therapist,
            record,
            starts_at=NOW - timedelta(days=2),
            status=AppointmentStatus.COMPLETED,
        )
        ended = await therapist_api.delete(f"/staff/clients/{record.id}")
        assert ended.status_code == 204

        response = await therapist_api.get("/staff/appointments")

        assert listed_ids(response) == ids_of(appointment)
        assert response.json()["items"][0]["client_last_name"] == "Yilmaz"

    async def test_an_admin_sees_all_of_them(
        self,
        admin_api: httpx.AsyncClient,
        therapist: User,
        colleague: User,
        record: Client,
        colleague_record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        first = await make_appointment(therapist, record)
        second = await make_appointment(
            colleague, colleague_record, starts_at=NOW + timedelta(days=2)
        )

        response = await admin_api.get("/staff/appointments")

        assert listed_ids(response) == ids_of(first, second)

    async def test_an_admin_may_filter_by_psychologist(
        self,
        admin_api: httpx.AsyncClient,
        therapist: User,
        colleague: User,
        record: Client,
        colleague_record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(therapist, record)
        second = await make_appointment(colleague, colleague_record)

        response = await admin_api.get(
            "/staff/appointments", params={"psychologist_id": str(colleague.id)}
        )

        assert listed_ids(response) == ids_of(second)

    async def test_an_admin_naming_no_psychologist_at_all_is_not_found(
        self, admin_api: httpx.AsyncClient
    ) -> None:
        response = await admin_api.get(
            "/staff/appointments", params={"psychologist_id": str(UNKNOWN_ID)}
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"from": "2026-12-08T07:50:00Z"}, ["late"]),
            ({"from": "2026-12-08T07:49:59Z"}, ["early", "late"]),
            ({"to": "2026-12-08T09:00:00Z"}, ["early"]),
            ({"to": "2026-12-08T09:00:01Z"}, ["early", "late"]),
            ({"from": "2026-12-08T08:00:00Z", "to": "2026-12-08T08:30:00Z"}, []),
            ({"from": "2026-12-08T10:00:00+03:00", "to": "2026-12-08T10:10:00+03:00"}, ["early"]),
        ],
        ids=[
            "from_at_the_end",
            "from_just_before_the_end",
            "to_at_the_start",
            "to_just_after_the_start",
            "gap_between_them",
            "other_offset",
        ],
    )
    async def test_from_and_to_select_overlapping_appointments(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        params: dict[str, str],
        expected: list[str],
    ) -> None:
        appointments = {
            "early": await make_appointment(therapist, record, starts_at=local(10)),
            "late": await make_appointment(therapist, record, starts_at=local(12)),
        }

        response = await therapist_api.get("/staff/appointments", params=params)

        assert listed_ids(response) == ids_of(*(appointments[name] for name in expected))

    async def test_a_reversed_range_is_refused(self, therapist_api: httpx.AsyncClient) -> None:
        response = await therapist_api.get(
            "/staff/appointments",
            params={"from": "2026-12-09T00:00:00Z", "to": "2026-12-08T00:00:00Z"},
        )

        assert response.status_code == 422
        assert error_code(response) == "DATE_RANGE_INVALID"

    async def test_a_naive_bound_is_refused(self, therapist_api: httpx.AsyncClient) -> None:
        response = await therapist_api.get(
            "/staff/appointments", params={"from": "2026-12-09T00:00:00"}
        )

        assert response.status_code == 422
        assert error_code(response) == "VALIDATION_ERROR"

    async def test_status_may_repeat(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        requested = await make_appointment(
            therapist, record, starts_at=local(9), status=AppointmentStatus.REQUESTED
        )
        await make_appointment(therapist, record, starts_at=local(11))
        no_show = await make_appointment(
            therapist, record, starts_at=local(13), status=AppointmentStatus.NO_SHOW
        )

        response = await therapist_api.get(
            "/staff/appointments", params={"status": ["requested", "no_show"]}
        )

        assert listed_ids(response) == ids_of(requested, no_show)
        assert response.json()["total"] == 2

    async def test_an_unknown_status_is_refused(self, therapist_api: httpx.AsyncClient) -> None:
        response = await therapist_api.get("/staff/appointments", params={"status": "lost"})

        assert response.status_code == 422

    async def test_it_orders_by_start_and_pages(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        latest = await make_appointment(therapist, record, starts_at=local(14))
        earliest = await make_appointment(therapist, record, starts_at=local(10))
        middle = await make_appointment(therapist, record, starts_at=local(12))

        everything = await therapist_api.get("/staff/appointments")
        page = await therapist_api.get("/staff/appointments", params={"limit": 1, "offset": 1})

        assert listed_ids(everything) == ids_of(earliest, middle, latest)
        assert listed_ids(page) == ids_of(middle)
        assert page.json()["total"] == 3

    async def test_more_rows_cost_no_more_queries(
        self,
        admin_api: httpx.AsyncClient,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
        make_appointment: AppointmentFactory,
        statements: list[str],
    ) -> None:
        async def add_appointment() -> None:
            psychologist = await make_user(UserRole.PSYCHOLOGIST)
            await make_appointment(psychologist, await make_client_record(psychologist))

        await add_appointment()
        statements.clear()
        await admin_api.get("/staff/appointments")
        with_one_row = len(statements)

        for _ in range(4):
            await add_appointment()
        statements.clear()
        response = await admin_api.get("/staff/appointments")

        assert response.json()["total"] == 5
        assert len(statements) == with_one_row


class TestDetail:
    async def test_a_psychologist_reads_their_own(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(therapist, record)

        response = await therapist_api.get(f"/staff/appointments/{appointment.id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(appointment.id)
        assert response.json()["client_first_name"] == "Zeynep"

    async def test_an_appointment_of_a_colleague_is_not_found(
        self,
        therapist_api: httpx.AsyncClient,
        colleague: User,
        colleague_record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(colleague, colleague_record)

        response = await therapist_api.get(f"/staff/appointments/{appointment.id}")

        assert response.status_code == 404
        assert error_code(response) == "NOT_FOUND"

    async def test_an_admin_reads_any_of_them(
        self,
        admin_api: httpx.AsyncClient,
        colleague: User,
        colleague_record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(colleague, colleague_record)

        response = await admin_api.get(f"/staff/appointments/{appointment.id}")

        assert response.status_code == 200

    async def test_an_unknown_id_is_not_found(self, admin_api: httpx.AsyncClient) -> None:
        response = await admin_api.get(f"/staff/appointments/{UNKNOWN_ID}")

        assert response.status_code == 404

    async def test_a_soft_deleted_client_keeps_their_name(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        appointment = await make_appointment(therapist, record)
        record.deleted_at = NOW
        await db_session.commit()

        response = await therapist_api.get(f"/staff/appointments/{appointment.id}")

        assert response.json()["client_last_name"] == "Yilmaz"


class TestCreate:
    async def create(
        self,
        api: httpx.AsyncClient,
        record: Client,
        service: Service,
        starts_at: datetime = SESSION_START,
    ) -> httpx.Response:
        return await api.post("/staff/appointments", json=staff_booking(record, service, starts_at))

    async def test_it_confirms_at_once_and_returns_the_staff_view(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
    ) -> None:
        response = await self.create(therapist_api, record, service)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "confirmed"
        assert body["booking_channel"] == "staff"
        assert body["booked_by_user_id"] == str(therapist.id)
        assert body["psychologist_id"] == str(therapist.id)
        assert body["client_id"] == str(record.id)
        assert body["client_first_name"] == "Zeynep"
        assert body["service_name"] == "Individual Therapy"
        assert body["ends_at"] == "2026-12-08T07:50:00Z"

    async def test_it_ignores_auto_confirmation(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        service: Service,
        profile: PsychologistProfile,
    ) -> None:
        assert profile.auto_confirm_bookings is False

        response = await self.create(therapist_api, record, service)

        assert response.json()["status"] == "confirmed"

    async def test_it_writes_one_history_row_and_one_audit_entry(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        response = await self.create(therapist_api, record, service)

        appointment_id = uuid.UUID(response.json()["id"])
        [row] = await history_of(db_session, appointment_id)
        assert row.from_status is None
        assert row.to_status is AppointmentStatus.CONFIRMED
        assert row.changed_by_user_id == therapist.id
        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_CREATED)
        assert entry.actor_user_id == therapist.id
        assert entry.entity_id == appointment_id
        assert entry.event_metadata == {"status": "confirmed", "booking_channel": "staff"}

    async def test_it_takes_the_effective_price(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_service: ServiceFactory,
        assign_service: ServiceAssigner,
    ) -> None:
        priced = await make_service(duration_minutes=50)
        await assign_service(therapist, priced, Decimal("750.00"))

        response = await self.create(therapist_api, record, priced)

        assert response.json()["price_amount"] == "750.00"

    async def test_a_client_never_in_care_is_not_found(
        self,
        therapist_api: httpx.AsyncClient,
        colleague_record: Client,
        service: Service,
    ) -> None:
        response = await self.create(therapist_api, colleague_record, service)

        assert response.status_code == 404
        assert error_code(response) == "NOT_FOUND"

    async def test_an_unknown_client_is_not_found(
        self, therapist_api: httpx.AsyncClient, service: Service
    ) -> None:
        body = {
            "client_id": str(UNKNOWN_ID),
            "service_id": str(service.id),
            "starts_at": local(10).isoformat(),
        }

        response = await therapist_api.post("/staff/appointments", json=body)

        assert response.status_code == 404

    async def test_an_ended_relationship_is_reopened_and_the_client_restored(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        assert (await therapist_api.delete(f"/staff/clients/{record.id}")).status_code == 204

        response = await self.create(therapist_api, record, service)

        assert response.status_code == 201
        relationship = await db_session.scalar(
            select(PsychologistClient)
            .where(PsychologistClient.client_id == record.id)
            .execution_options(populate_existing=True)
        )
        assert relationship is not None
        assert relationship.psychologist_id == therapist.id
        assert relationship.ended_at is None
        await db_session.refresh(record)
        assert record.deleted_at is None
        [entry] = await audit_entries(db_session, AuditAction.CLIENT_UPDATED)
        assert entry.actor_user_id == therapist.id

    async def test_a_service_they_do_not_offer_is_not_found(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        make_service: ServiceFactory,
    ) -> None:
        unassigned = await make_service()

        response = await self.create(therapist_api, record, unassigned)

        assert response.status_code == 404

    async def test_a_deactivated_service_is_not_found(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        service.is_active = False
        await db_session.commit()

        response = await self.create(therapist_api, record, service)

        assert response.status_code == 404

    async def test_it_needs_no_working_hours(
        self, therapist_api: httpx.AsyncClient, record: Client, service: Service
    ) -> None:
        sunday_night = datetime(2026, 12, 6, 23, 7, tzinfo=UTC)

        response = await self.create(therapist_api, record, service, sunday_night)

        assert response.status_code == 201

    async def test_it_ignores_the_notice_limit(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        service: Service,
        clock: FakeClock,
    ) -> None:
        response = await self.create(
            therapist_api, record, service, clock.now + timedelta(seconds=1)
        )

        assert response.status_code == 201

    async def test_it_ignores_the_advance_limit(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        service: Service,
        clock: FakeClock,
    ) -> None:
        response = await self.create(
            therapist_api, record, service, clock.now + timedelta(days=400)
        )

        assert response.status_code == 201

    async def test_it_ignores_the_buffer(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        profile: PsychologistProfile,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.buffer_minutes = 60
        await db_session.commit()
        await make_appointment(therapist, record, starts_at=local(9), ends_at=local(9, 50))

        response = await self.create(therapist_api, record, service, local(9, 50))

        assert response.status_code == 201

    @pytest.mark.parametrize("seconds", [0, -1, -86_400])
    async def test_a_start_at_or_before_now_is_unavailable(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        service: Service,
        clock: FakeClock,
        seconds: int,
    ) -> None:
        response = await self.create(
            therapist_api, record, service, clock.now + timedelta(seconds=seconds)
        )

        assert response.status_code == 409
        assert error_code(response) == "SLOT_UNAVAILABLE"

    async def test_time_off_conflicts(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist, starts_at=local(10, 30), ends_at=local(11))

        response = await self.create(therapist_api, record, service, local(10))

        assert response.status_code == 409
        assert error_code(response) == "TIME_OFF_CONFLICT"

    async def test_a_session_may_end_when_time_off_starts(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist, starts_at=local(10, 50), ends_at=local(11))

        response = await self.create(therapist_api, record, service, local(10))

        assert response.status_code == 201

    async def test_an_overlap_is_taken(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        service: Service,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(10))

        response = await self.create(therapist_api, record, service, local(10, 20))

        assert response.status_code == 409
        assert error_code(response) == "SLOT_TAKEN"

    @pytest.mark.parametrize(
        "changes",
        [
            {"starts_at": "2026-12-08T10:00:00"},
            {"client_id": "not-a-uuid"},
            {"service_id": None},
        ],
        ids=["naive_start", "bad_client_id", "null_service"],
    )
    async def test_a_malformed_body_is_rejected(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        service: Service,
        changes: dict[str, Any],
    ) -> None:
        body = staff_booking(record, service, local(10)) | changes

        response = await therapist_api.post("/staff/appointments", json=body)

        assert response.status_code == 422


class TestReschedule:
    async def move(
        self,
        api: httpx.AsyncClient,
        appointment: Appointment,
        starts_at: datetime = MOVE_TARGET,
    ) -> httpx.Response:
        return await api.post(
            f"/staff/appointments/{appointment.id}/reschedule",
            json={"starts_at": starts_at.isoformat()},
        )

    @pytest.fixture
    async def appointment(
        self,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        service: Service,
    ) -> Appointment:
        """A session that was booked online, at an agreed price."""
        return await make_appointment(
            therapist,
            record,
            service_id=service.id,
            starts_at=local(10),
            ends_at=local(10, 50),
            price_amount=Decimal("1200.00"),
            status=AppointmentStatus.REQUESTED,
            booking_channel=BookingChannel.ONLINE,
            booked_by_user_id=record.created_by_user_id,
        )

    async def test_it_returns_the_new_appointment_with_201(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        appointment: Appointment,
    ) -> None:
        response = await self.move(therapist_api, appointment)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "confirmed"
        assert body["booking_channel"] == "staff"
        assert body["booked_by_user_id"] == str(therapist.id)
        assert body["rescheduled_from_id"] == str(appointment.id)
        assert body["starts_at"] == "2026-12-08T09:00:00Z"
        assert body["ends_at"] == "2026-12-08T09:50:00Z"

    async def test_it_keeps_the_price_and_duration(
        self,
        therapist_api: httpx.AsyncClient,
        appointment: Appointment,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        service.price_amount = Decimal("9000.00")
        service.duration_minutes = 120
        service.is_active = False
        await db_session.commit()

        response = await self.move(therapist_api, appointment)

        assert response.status_code == 201
        assert response.json()["price_amount"] == "1200.00"
        assert response.json()["ends_at"] == "2026-12-08T09:50:00Z"

    async def test_it_cancels_the_old_one_as_rescheduled(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        appointment: Appointment,
        db_session: AsyncSession,
    ) -> None:
        await self.move(therapist_api, appointment)

        old = await load_appointment(db_session, appointment.id)
        assert old.status is AppointmentStatus.CANCELLED
        assert old.cancelled_by_user_id == therapist.id
        assert old.cancellation_reason is CancellationReason.RESCHEDULED

    async def test_it_writes_two_history_rows_and_one_audit_entry(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        appointment: Appointment,
        db_session: AsyncSession,
    ) -> None:
        response = await self.move(therapist_api, appointment)

        new_id = uuid.UUID(response.json()["id"])
        [old_row] = await history_of(db_session, appointment.id)
        assert old_row.from_status is AppointmentStatus.REQUESTED
        assert old_row.reason_category is CancellationReason.RESCHEDULED
        [new_row] = await history_of(db_session, new_id)
        assert new_row.from_status is None
        assert new_row.to_status is AppointmentStatus.CONFIRMED
        assert new_row.reason_category is CancellationReason.RESCHEDULED
        assert new_row.changed_by_user_id == therapist.id
        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_RESCHEDULED)
        assert entry.actor_user_id == therapist.id
        assert entry.entity_id == appointment.id
        assert entry.event_metadata["new_appointment_id"] == str(new_id)
        assert await audit_entries(db_session, AuditAction.APPOINTMENT_CREATED) == []
        assert await audit_entries(db_session, AuditAction.APPOINTMENT_STATUS_CHANGED) == []

    async def test_no_window_applies(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        clock: FakeClock,
    ) -> None:
        soon = await make_appointment(
            therapist, record, starts_at=clock.now + timedelta(minutes=30)
        )

        response = await self.move(therapist_api, soon)

        assert response.status_code == 201

    async def test_it_needs_no_working_hours(
        self, therapist_api: httpx.AsyncClient, appointment: Appointment
    ) -> None:
        response = await self.move(
            therapist_api, appointment, datetime(2026, 12, 6, 23, 7, tzinfo=UTC)
        )

        assert response.status_code == 201

    async def test_it_ignores_the_buffer(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        appointment: Appointment,
        profile: PsychologistProfile,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.buffer_minutes = 60
        await db_session.commit()
        await make_appointment(therapist, record, starts_at=local(11), ends_at=local(11, 50))

        response = await self.move(therapist_api, appointment, local(11, 50))

        assert response.status_code == 201

    async def test_it_may_move_inside_its_own_old_range(
        self, therapist_api: httpx.AsyncClient, appointment: Appointment
    ) -> None:
        response = await self.move(therapist_api, appointment, local(10, 15))

        assert response.status_code == 201

    @pytest.mark.parametrize("seconds", [0, -3600])
    async def test_a_start_at_or_before_now_leaves_the_old_one_active(
        self,
        therapist_api: httpx.AsyncClient,
        appointment: Appointment,
        clock: FakeClock,
        db_session: AsyncSession,
        seconds: int,
    ) -> None:
        response = await self.move(
            therapist_api, appointment, clock.now + timedelta(seconds=seconds)
        )

        assert response.status_code == 409
        assert error_code(response) == "SLOT_UNAVAILABLE"
        old = await load_appointment(db_session, appointment.id)
        assert old.status is AppointmentStatus.REQUESTED

    async def test_time_off_conflicts_and_leaves_the_old_one_active(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        appointment: Appointment,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        await make_time_off(therapist, starts_at=local(12), ends_at=local(13))

        response = await self.move(therapist_api, appointment)

        assert error_code(response) == "TIME_OFF_CONFLICT"
        old = await load_appointment(db_session, appointment.id)
        assert old.status is AppointmentStatus.REQUESTED
        assert await history_of(db_session, appointment.id) == []

    async def test_a_taken_time_leaves_the_old_one_active(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        appointment: Appointment,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(12))

        response = await self.move(therapist_api, appointment)

        assert error_code(response) == "SLOT_TAKEN"
        old = await load_appointment(db_session, appointment.id)
        assert old.status is AppointmentStatus.REQUESTED
        assert old.cancelled_at is None
        assert await history_of(db_session, appointment.id) == []
        assert await audit_entries(db_session, AuditAction.APPOINTMENT_RESCHEDULED) == []

    @pytest.mark.parametrize(
        "status",
        [AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW],
    )
    async def test_a_final_status_cannot_move(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        status: AppointmentStatus,
    ) -> None:
        closed = await make_appointment(therapist, record, starts_at=local(10), **in_status(status))

        response = await self.move(therapist_api, closed)

        assert error_code(response) == "INVALID_STATUS_TRANSITION"

    async def test_an_appointment_of_a_colleague_is_not_found(
        self,
        therapist_api: httpx.AsyncClient,
        colleague: User,
        colleague_record: Client,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        foreign = await make_appointment(colleague, colleague_record, starts_at=local(10))

        response = await self.move(therapist_api, foreign)

        assert response.status_code == 404
        loaded = await load_appointment(db_session, foreign.id)
        assert loaded.status is AppointmentStatus.CONFIRMED

    async def test_it_reopens_care_for_a_session_left_open_in_the_past(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        past = await make_appointment(therapist, record, starts_at=NOW - timedelta(days=2))
        assert (await therapist_api.delete(f"/staff/clients/{record.id}")).status_code == 204

        response = await self.move(therapist_api, past)

        assert response.status_code == 201
        await db_session.refresh(record)
        assert record.deleted_at is None
