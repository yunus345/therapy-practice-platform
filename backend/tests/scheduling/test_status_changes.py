from datetime import timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.clients.models import Client
from app.modules.scheduling.models import Appointment, AppointmentStatus, CancellationReason
from app.modules.users.models import User, UserRole
from tests.conftest import (
    UNKNOWN_ID,
    AppointmentFactory,
    ClientRecordFactory,
    UserFactory,
    audit_entries,
)
from tests.scheduling.conftest import (
    NOW,
    Booker,
    error_code,
    history_of,
    in_status,
    load_appointment,
)

pytestmark = pytest.mark.usefixtures("fixed_time")

REQUESTED = AppointmentStatus.REQUESTED
CONFIRMED = AppointmentStatus.CONFIRMED
COMPLETED = AppointmentStatus.COMPLETED
CANCELLED = AppointmentStatus.CANCELLED
NO_SHOW = AppointmentStatus.NO_SHOW

ALLOWED = {
    (REQUESTED, CONFIRMED),
    (REQUESTED, CANCELLED),
    (CONFIRMED, COMPLETED),
    (CONFIRMED, CANCELLED),
    (CONFIRMED, NO_SHOW),
}
EVERY_PAIR = [
    (from_status, to_status)
    for from_status in AppointmentStatus
    for to_status in (CONFIRMED, COMPLETED, CANCELLED, NO_SHOW)
]


@pytest.fixture
async def record(therapist: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(therapist)


@pytest.fixture
async def session_started(
    therapist: User, record: Client, make_appointment: AppointmentFactory
) -> Appointment:
    """A confirmed session that began an hour ago."""
    return await make_appointment(therapist, record, starts_at=NOW - timedelta(hours=1))


@pytest.fixture
async def session_ahead(
    therapist: User, record: Client, make_appointment: AppointmentFactory
) -> Appointment:
    """A confirmed session that begins in an hour, deep inside the client's window."""
    return await make_appointment(therapist, record, starts_at=NOW + timedelta(hours=1))


async def change(
    api: httpx.AsyncClient, appointment: Appointment, status: str, reason: str | None = None
) -> httpx.Response:
    body = {"status": status}
    if reason is not None:
        body["cancellation_reason"] = reason
    return await api.post(f"/staff/appointments/{appointment.id}/status", json=body)


class TestEveryPairOfStatuses:
    @pytest.mark.parametrize(("from_status", "to_status"), EVERY_PAIR)
    async def test_only_the_allowed_changes_go_through(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        from_status: AppointmentStatus,
        to_status: AppointmentStatus,
    ) -> None:
        appointment = await make_appointment(
            therapist, record, starts_at=NOW - timedelta(hours=1), **in_status(from_status)
        )

        response = await change(therapist_api, appointment, to_status)

        loaded = await load_appointment(db_session, appointment.id)
        history = await history_of(db_session, appointment.id)
        entries = await audit_entries(db_session, AuditAction.APPOINTMENT_STATUS_CHANGED)
        if (from_status, to_status) in ALLOWED:
            assert response.status_code == 200
            assert response.json()["status"] == to_status
            assert loaded.status is to_status
            [row] = history
            assert row.from_status is from_status
            assert row.to_status is to_status
            assert len(entries) == 1
        else:
            assert response.status_code == 409
            assert error_code(response) == "INVALID_STATUS_TRANSITION"
            assert loaded.status is from_status
            assert history == []
            assert entries == []

    @pytest.mark.parametrize("status", ["requested", "lost", "", None])
    async def test_a_status_outside_the_list_is_rejected(
        self,
        therapist_api: httpx.AsyncClient,
        session_started: Appointment,
        status: str | None,
    ) -> None:
        response = await therapist_api.post(
            f"/staff/appointments/{session_started.id}/status", json={"status": status}
        )

        assert response.status_code == 422
        assert error_code(response) == "VALIDATION_ERROR"


class TestTiming:
    @pytest.mark.parametrize("status", [COMPLETED, NO_SHOW])
    async def test_an_outcome_is_refused_before_the_start(
        self,
        therapist_api: httpx.AsyncClient,
        session_ahead: Appointment,
        db_session: AsyncSession,
        status: AppointmentStatus,
    ) -> None:
        response = await change(therapist_api, session_ahead, status)

        assert response.status_code == 409
        assert error_code(response) == "APPOINTMENT_NOT_STARTED"
        loaded = await load_appointment(db_session, session_ahead.id)
        assert loaded.status is CONFIRMED
        assert await history_of(db_session, session_ahead.id) == []

    @pytest.mark.parametrize("status", [COMPLETED, NO_SHOW])
    async def test_an_outcome_is_allowed_from_the_exact_start(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        status: AppointmentStatus,
    ) -> None:
        appointment = await make_appointment(therapist, record, starts_at=NOW)

        response = await change(therapist_api, appointment, status)

        assert response.status_code == 200

    async def test_a_requested_session_ahead_reports_the_status_first(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(
            therapist, record, starts_at=NOW + timedelta(hours=1), status=REQUESTED
        )

        response = await change(therapist_api, appointment, COMPLETED)

        assert error_code(response) == "INVALID_STATUS_TRANSITION"

    @pytest.mark.parametrize("status", [CONFIRMED, CANCELLED])
    async def test_other_changes_need_no_start(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        status: AppointmentStatus,
    ) -> None:
        appointment = await make_appointment(
            therapist, record, starts_at=NOW + timedelta(hours=1), status=REQUESTED
        )

        response = await change(therapist_api, appointment, status)

        assert response.status_code == 200


class TestCancellationReason:
    async def test_the_default_is_a_psychologist_request(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        session_ahead: Appointment,
        db_session: AsyncSession,
    ) -> None:
        response = await change(therapist_api, session_ahead, CANCELLED)

        assert response.status_code == 200
        assert response.json()["cancellation_reason"] == "psychologist_request"
        assert response.json()["cancelled_at"] == "2026-11-30T06:00:00Z"
        assert response.json()["cancelled_by_user_id"] == str(therapist.id)
        loaded = await load_appointment(db_session, session_ahead.id)
        assert loaded.cancelled_at == NOW
        assert loaded.cancelled_by_user_id == therapist.id
        assert loaded.cancellation_reason is CancellationReason.PSYCHOLOGIST_REQUEST

    @pytest.mark.parametrize(
        "reason",
        [
            CancellationReason.CLIENT_REQUEST,
            CancellationReason.PSYCHOLOGIST_REQUEST,
            CancellationReason.LATE_CANCELLATION,
            CancellationReason.OTHER,
        ],
    )
    async def test_staff_may_name_a_reason(
        self,
        therapist_api: httpx.AsyncClient,
        session_ahead: Appointment,
        db_session: AsyncSession,
        reason: CancellationReason,
    ) -> None:
        response = await change(therapist_api, session_ahead, CANCELLED, reason)

        assert response.json()["cancellation_reason"] == reason
        [row] = await history_of(db_session, session_ahead.id)
        assert row.reason_category is reason

    async def test_rescheduled_is_not_a_reason_staff_can_give(
        self, therapist_api: httpx.AsyncClient, session_ahead: Appointment
    ) -> None:
        response = await change(therapist_api, session_ahead, CANCELLED, "rescheduled")

        assert response.status_code == 422
        assert error_code(response) == "VALIDATION_ERROR"

    @pytest.mark.parametrize("status", [CONFIRMED, COMPLETED, NO_SHOW])
    async def test_a_reason_needs_a_cancellation(
        self,
        therapist_api: httpx.AsyncClient,
        session_started: Appointment,
        status: AppointmentStatus,
    ) -> None:
        response = await change(therapist_api, session_started, status, "other")

        assert response.status_code == 422
        assert error_code(response) == "VALIDATION_ERROR"

    async def test_an_unknown_reason_is_rejected(
        self, therapist_api: httpx.AsyncClient, session_ahead: Appointment
    ) -> None:
        response = await change(therapist_api, session_ahead, CANCELLED, "no reason at all")

        assert response.status_code == 422

    async def test_a_change_that_is_no_cancellation_sets_no_cancellation_fields(
        self,
        therapist_api: httpx.AsyncClient,
        session_started: Appointment,
        db_session: AsyncSession,
    ) -> None:
        await change(therapist_api, session_started, COMPLETED)

        loaded = await load_appointment(db_session, session_started.id)
        assert loaded.cancelled_at is None
        assert loaded.cancelled_by_user_id is None
        assert loaded.cancellation_reason is None
        [row] = await history_of(db_session, session_started.id)
        assert row.reason_category is None


class TestLateCancellation:
    async def test_only_staff_can_record_one_after_the_window_closed(
        self,
        therapist_api: httpx.AsyncClient,
        booker: Booker,
        therapist: User,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(
            therapist, booker.record, starts_at=NOW + timedelta(hours=1)
        )

        by_client = await booker.api.post(f"/appointments/{appointment.id}/cancel")
        by_staff = await change(
            therapist_api, appointment, CANCELLED, CancellationReason.LATE_CANCELLATION
        )

        assert error_code(by_client) == "CANCELLATION_WINDOW_CLOSED"
        assert by_staff.status_code == 200
        assert by_staff.json()["cancellation_reason"] == "late_cancellation"

    async def test_it_works_after_the_session_should_have_begun(
        self, therapist_api: httpx.AsyncClient, session_started: Appointment
    ) -> None:
        response = await change(
            therapist_api, session_started, CANCELLED, CancellationReason.LATE_CANCELLATION
        )

        assert response.status_code == 200


class TestRecords:
    async def test_a_change_writes_one_history_row(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        session_ahead: Appointment,
        db_session: AsyncSession,
    ) -> None:
        await change(therapist_api, session_ahead, CANCELLED)

        [row] = await history_of(db_session, session_ahead.id)
        assert row.from_status is CONFIRMED
        assert row.to_status is CANCELLED
        assert row.changed_by_user_id == therapist.id
        assert row.changed_at == NOW
        assert row.reason_category is CancellationReason.PSYCHOLOGIST_REQUEST

    async def test_a_change_writes_one_audit_entry_without_personal_data(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        session_ahead: Appointment,
        db_session: AsyncSession,
    ) -> None:
        await change(therapist_api, session_ahead, CANCELLED)

        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_STATUS_CHANGED)
        assert entry.actor_user_id == therapist.id
        assert entry.entity_type == "appointment"
        assert entry.entity_id == session_ahead.id
        assert entry.event_metadata == {
            "from_status": "confirmed",
            "to_status": "cancelled",
            "reason_category": "psychologist_request",
        }

    async def test_a_confirmation_has_no_reason_in_its_audit_entry(
        self,
        therapist_api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        appointment = await make_appointment(
            therapist, record, starts_at=NOW + timedelta(hours=1), status=REQUESTED
        )

        await change(therapist_api, appointment, CONFIRMED)

        [entry] = await audit_entries(db_session, AuditAction.APPOINTMENT_STATUS_CHANGED)
        assert entry.event_metadata == {
            "from_status": "requested",
            "to_status": "confirmed",
            "reason_category": None,
        }

    async def test_the_response_is_the_updated_staff_view(
        self,
        therapist_api: httpx.AsyncClient,
        record: Client,
        session_ahead: Appointment,
    ) -> None:
        response = await change(therapist_api, session_ahead, CANCELLED)

        assert response.json()["id"] == str(session_ahead.id)
        assert response.json()["client_last_name"] == record.last_name


class TestScope:
    async def test_an_appointment_of_a_colleague_is_not_found_and_stays_as_it_is(
        self,
        therapist_api: httpx.AsyncClient,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        colleague = await make_user(UserRole.PSYCHOLOGIST)
        foreign = await make_appointment(colleague, await make_client_record(colleague))

        response = await change(therapist_api, foreign, CANCELLED)

        assert response.status_code == 404
        assert error_code(response) == "NOT_FOUND"
        assert (await load_appointment(db_session, foreign.id)).status is CONFIRMED

    async def test_an_unknown_appointment_is_not_found(
        self, therapist_api: httpx.AsyncClient
    ) -> None:
        response = await therapist_api.post(
            f"/staff/appointments/{UNKNOWN_ID}/status", json={"status": "cancelled"}
        )

        assert response.status_code == 404
