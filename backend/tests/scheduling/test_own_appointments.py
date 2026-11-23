from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import Appointment, AppointmentStatus
from app.modules.users.models import PsychologistProfile, User, UserRole
from tests.conftest import (
    AppointmentFactory,
    ClientFactory,
    ClientRecordFactory,
    FakeClock,
    UserFactory,
    log_in,
)
from tests.scheduling.conftest import (
    NOW,
    Booker,
    BookerFactory,
    RuleFactory,
    book,
    ids_of,
    in_status,
    listed_ids,
    local,
)

pytestmark = pytest.mark.usefixtures("fixed_time")


class TestFilters:
    @pytest.fixture
    async def history(
        self, booker: Booker, therapist: User, make_appointment: AppointmentFactory
    ) -> dict[str, Appointment]:
        async def appointment(hours: float, **fields: Any) -> Appointment:
            return await make_appointment(
                therapist, booker.record, starts_at=NOW + timedelta(hours=hours), **fields
            )

        return {
            "later": await appointment(72),
            "soon": await appointment(24, status=AppointmentStatus.REQUESTED),
            "in_progress": await appointment(-0.5),
            "ended": await appointment(-24),
            "completed": await appointment(-48, status=AppointmentStatus.COMPLETED),
            "no_show": await appointment(-72, status=AppointmentStatus.NO_SHOW),
            "cancelled_ahead": await appointment(48, **in_status(AppointmentStatus.CANCELLED)),
        }

    async def test_upcoming_is_active_and_unfinished_soonest_first(
        self, booker: Booker, history: dict[str, Appointment]
    ) -> None:
        response = await booker.api.get("/me/appointments", params={"when": "upcoming"})

        assert listed_ids(response) == ids_of(
            history["in_progress"], history["soon"], history["later"]
        )
        assert response.json()["total"] == 3

    async def test_past_is_everything_else_latest_first(
        self, booker: Booker, history: dict[str, Appointment]
    ) -> None:
        response = await booker.api.get("/me/appointments", params={"when": "past"})

        assert listed_ids(response) == ids_of(
            history["cancelled_ahead"],
            history["ended"],
            history["completed"],
            history["no_show"],
        )
        assert response.json()["total"] == 4

    @pytest.mark.parametrize("params", [{}, {"when": "all"}], ids=["default", "explicit"])
    async def test_all_lists_everything_latest_first(
        self, booker: Booker, history: dict[str, Appointment], params: dict[str, str]
    ) -> None:
        response = await booker.api.get("/me/appointments", params=params)

        assert listed_ids(response) == ids_of(
            history["later"],
            history["cancelled_ahead"],
            history["soon"],
            history["in_progress"],
            history["ended"],
            history["completed"],
            history["no_show"],
        )

    async def test_an_appointment_turns_past_the_moment_it_ends(
        self,
        booker: Booker,
        therapist: User,
        clock: FakeClock,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointment = await make_appointment(
            therapist,
            booker.record,
            starts_at=NOW - timedelta(minutes=49),
            ends_at=NOW + timedelta(minutes=1),
        )
        before = await booker.api.get("/me/appointments", params={"when": "upcoming"})

        clock.advance(minutes=1)
        after = await booker.api.get("/me/appointments", params={"when": "past"})

        assert listed_ids(before) == ids_of(appointment)
        assert listed_ids(after) == ids_of(appointment)

    async def test_an_unknown_filter_is_rejected(self, booker: Booker) -> None:
        response = await booker.api.get("/me/appointments", params={"when": "someday"})

        assert response.status_code == 422


class TestScope:
    async def test_it_lists_only_the_callers_appointments(
        self,
        booker: Booker,
        make_booker: BookerFactory,
        therapist: User,
        make_appointment: AppointmentFactory,
    ) -> None:
        other = await make_booker()
        own = await make_appointment(therapist, booker.record)
        foreign = await make_appointment(therapist, other.record, starts_at=NOW + timedelta(days=3))

        response = await booker.api.get("/me/appointments")

        assert listed_ids(response) == ids_of(own)
        assert str(foreign.id) not in response.text
        assert response.json()["total"] == 1

    async def test_an_account_without_a_client_record_has_no_appointments(
        self, new_client: ClientFactory, make_user: UserFactory
    ) -> None:
        client = await new_client()
        await log_in(client, (await make_user(UserRole.CLIENT)).email)

        response = await client.get("/me/appointments")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def test_a_soft_deleted_client_still_sees_their_history(
        self,
        booker: Booker,
        therapist: User,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        appointment = await make_appointment(therapist, booker.record)
        booker.record.deleted_at = NOW
        await db_session.commit()

        response = await booker.api.get("/me/appointments")

        assert listed_ids(response) == ids_of(appointment)


class TestItems:
    async def test_they_hold_exactly_what_a_client_may_see(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
    ) -> None:
        cancelled = await make_appointment(
            therapist,
            booker.record,
            service_id=service.id,
            starts_at=local(10),
            ends_at=local(10, 50),
            **in_status(AppointmentStatus.CANCELLED),
        )

        response = await booker.api.get("/me/appointments")

        [item] = response.json()["items"]
        assert item == {
            "id": str(cancelled.id),
            "psychologist_name": "A B",
            "psychologist_slug": profile.slug,
            "service_name": "Individual Therapy",
            "delivery_mode": "in_person",
            "starts_at": "2026-12-08T07:00:00Z",
            "ends_at": "2026-12-08T07:50:00Z",
            "timezone": "Europe/Istanbul",
            "status": "cancelled",
            "price_amount": "1500.00",
            "currency": "TRY",
            "rescheduled_from_id": None,
            "cancelled_at": "2026-11-30T06:00:00Z",
            "cancellation_reason": "other",
            "created_at": item["created_at"],
        }

    async def test_a_deactivated_service_keeps_its_name_in_history(
        self,
        booker: Booker,
        therapist: User,
        service: Service,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        await make_appointment(therapist, booker.record, service_id=service.id)
        service.is_active = False
        await db_session.commit()

        response = await booker.api.get("/me/appointments")

        assert response.json()["items"][0]["service_name"] == "Individual Therapy"

    async def test_a_reschedule_links_the_new_appointment_to_the_old_one(
        self,
        booker: Booker,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist)
        booked = (await book(booker, profile, service)).json()
        moved = await booker.api.post(
            f"/appointments/{booked['id']}/reschedule",
            json={"starts_at": local(12).isoformat()},
        )

        response = await booker.api.get("/me/appointments", params={"when": "upcoming"})

        [item] = response.json()["items"]
        assert item["id"] == moved.json()["id"]
        assert item["rescheduled_from_id"] == booked["id"]


class TestPagination:
    async def test_limit_and_offset_cut_the_filtered_list(
        self,
        booker: Booker,
        therapist: User,
        make_appointment: AppointmentFactory,
    ) -> None:
        appointments = [
            await make_appointment(therapist, booker.record, starts_at=NOW + timedelta(days=day))
            for day in (1, 2, 3)
        ]

        response = await booker.api.get(
            "/me/appointments", params={"when": "upcoming", "limit": 1, "offset": 1}
        )

        assert listed_ids(response) == ids_of(appointments[1])
        assert response.json()["total"] == 3
        assert response.json()["limit"] == 1
        assert response.json()["offset"] == 1

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    async def test_page_parameters_are_validated(
        self, booker: Booker, params: dict[str, int]
    ) -> None:
        response = await booker.api.get("/me/appointments", params=params)

        assert response.status_code == 422


async def test_more_rows_cost_no_more_queries(
    booker: Booker,
    therapist: User,
    make_user: UserFactory,
    make_appointment: AppointmentFactory,
    statements: list[str],
) -> None:
    await make_appointment(therapist, booker.record)
    statements.clear()
    await booker.api.get("/me/appointments")
    with_one_row = len(statements)

    for day in range(2, 6):
        colleague = await make_user(UserRole.PSYCHOLOGIST)
        await make_appointment(colleague, booker.record, starts_at=NOW + timedelta(days=day))
    statements.clear()
    response = await booker.api.get("/me/appointments")

    assert response.json()["total"] == 5
    assert len(statements) == with_one_row


async def test_records_of_other_clients_do_not_reach_the_list(
    booker: Booker,
    therapist: User,
    make_client_record: ClientRecordFactory,
    make_appointment: AppointmentFactory,
) -> None:
    other: Client = await make_client_record(therapist, first_name="Elif", last_name="Demir")
    await make_appointment(therapist, other)

    response = await booker.api.get("/me/appointments")

    assert "Elif" not in response.text
    assert response.json()["items"] == []
