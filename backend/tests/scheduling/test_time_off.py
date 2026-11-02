import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.clients.models import Client
from app.modules.scheduling.models import AppointmentStatus, CancellationReason, TimeOff
from app.modules.users.models import User
from tests.conftest import UNKNOWN_ID, AppointmentFactory, ClientRecordFactory, audit_entries
from tests.scheduling.conftest import PERIOD_END, PERIOD_START, TimeOffFactory

TIME_OFF = "/staff/time-off"
BODY: dict[str, Any] = {
    "starts_at": "2026-12-08T10:00:00Z",
    "ends_at": "2026-12-08T11:00:00Z",
    "reason": "Conference",
}


def on_tuesday(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 12, 8, hour, minute, tzinfo=UTC)


async def load_time_off(session: AsyncSession, time_off_id: uuid.UUID) -> TimeOff | None:
    return await session.get(TimeOff, time_off_id, populate_existing=True)


@pytest.fixture
async def record(psychologist: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(psychologist)


class TestCreate:
    async def test_psychologist_creates_time_off_for_themselves(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        response = await api.post(TIME_OFF, json=BODY)

        assert response.status_code == 201
        body = response.json()
        assert body["psychologist_id"] == str(psychologist.id)
        assert body["reason"] == "Conference"
        loaded = await load_time_off(db_session, uuid.UUID(body["id"]))
        assert loaded is not None
        assert (loaded.starts_at, loaded.ends_at) == (PERIOD_START, PERIOD_END)

    async def test_the_reason_is_optional(self, api: httpx.AsyncClient, psychologist: User) -> None:
        body = {name: value for name, value in BODY.items() if name != "reason"}

        response = await api.post(TIME_OFF, json=body)

        assert response.status_code == 201
        assert response.json()["reason"] is None

    async def test_times_are_normalized_to_utc(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.post(
            TIME_OFF,
            json=BODY
            | {"starts_at": "2026-12-08T13:00:00+03:00", "ends_at": "2026-12-08T14:00:00+03:00"},
        )

        assert response.status_code == 201
        assert response.json()["starts_at"] == "2026-12-08T10:00:00Z"
        assert response.json()["ends_at"] == "2026-12-08T11:00:00Z"

    @pytest.mark.parametrize(
        "change",
        [
            {"starts_at": "2026-12-08T10:00:00"},
            {"ends_at": "2026-12-08T11:00:00"},
            {"starts_at": "soon"},
            {"starts_at": "0001-01-01T00:00:00+03:00"},
            {"ends_at": "9999-12-31T23:59:59-03:00"},
            {"reason": ""},
            {"reason": "   "},
            {"reason": "a" * 201},
            {"reason": "a\x00b"},
        ],
    )
    async def test_invalid_values_are_rejected(
        self, api: httpx.AsyncClient, psychologist: User, change: dict[str, Any]
    ) -> None:
        response = await api.post(TIME_OFF, json=BODY | change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_a_reason_may_use_the_whole_length(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.post(TIME_OFF, json=BODY | {"reason": "a" * 200})

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "ends_at", ["2026-12-08T10:00:00Z", "2026-12-08T09:00:00Z", "2026-12-08T13:00:00+03:00"]
    )
    async def test_a_period_that_does_not_move_forward_is_rejected(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        db_session: AsyncSession,
        ends_at: str,
    ) -> None:
        response = await api.post(TIME_OFF, json=BODY | {"ends_at": ends_at})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_TIME_RANGE"
        assert await audit_entries(db_session, AuditAction.TIME_OFF_CREATED) == []

    async def test_creation_is_audited_without_the_reason(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        response = await api.post(TIME_OFF, json=BODY)

        [entry] = await audit_entries(db_session, AuditAction.TIME_OFF_CREATED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "time_off"
        assert str(entry.entity_id) == response.json()["id"]
        assert str(entry.ip_address) == "127.0.0.1"
        assert entry.event_metadata == {}


class TestConflictWithAppointments:
    @pytest.mark.parametrize("status", [AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
    async def test_an_active_appointment_blocks_new_time_off(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
        status: AppointmentStatus,
    ) -> None:
        await make_appointment(psychologist, record, starts_at=on_tuesday(10), status=status)

        response = await api.post(TIME_OFF, json=BODY)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "TIME_OFF_CONFLICT"
        assert await audit_entries(db_session, AuditAction.TIME_OFF_CREATED) == []
        assert await db_session.scalar(select(TimeOff.id)) is None

    async def test_the_response_counts_appointments_and_reveals_nothing_else(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(psychologist, record, starts_at=on_tuesday(9, 30))
        await make_appointment(psychologist, record, starts_at=on_tuesday(10, 30))

        response = await api.post(TIME_OFF, json=BODY)

        assert response.json()["error"]["details"] == {"appointment_count": 2}
        assert record.last_name not in response.text
        assert str(record.id) not in response.text

    @pytest.mark.parametrize(
        "fields",
        [
            {"status": AppointmentStatus.COMPLETED},
            {"status": AppointmentStatus.NO_SHOW},
            {
                "status": AppointmentStatus.CANCELLED,
                "cancelled_at": on_tuesday(1),
                "cancellation_reason": CancellationReason.CLIENT_REQUEST,
            },
        ],
        ids=["completed", "no_show", "cancelled"],
    )
    async def test_a_closed_appointment_does_not_block(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        fields: dict[str, Any],
    ) -> None:
        await make_appointment(psychologist, record, starts_at=on_tuesday(10), **fields)

        response = await api.post(TIME_OFF, json=BODY)

        assert response.status_code == 201

    @pytest.mark.parametrize(
        ("starts_at", "ends_at"),
        [(on_tuesday(9), on_tuesday(10)), (on_tuesday(11), on_tuesday(12))],
        ids=["appointment_before", "appointment_after"],
    )
    async def test_time_off_may_touch_an_appointment(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        starts_at: datetime,
        ends_at: datetime,
    ) -> None:
        await make_appointment(psychologist, record, starts_at=starts_at, ends_at=ends_at)

        response = await api.post(TIME_OFF, json=BODY)

        assert response.status_code == 201

    @pytest.mark.parametrize(
        ("starts_at", "ends_at"),
        [
            (on_tuesday(9), on_tuesday(10, 1)),
            (on_tuesday(10, 59), on_tuesday(12)),
            (on_tuesday(10, 20), on_tuesday(10, 40)),
            (on_tuesday(9), on_tuesday(12)),
        ],
        ids=["overlaps_the_start", "overlaps_the_end", "inside", "around"],
    )
    async def test_any_overlap_with_an_appointment_conflicts(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        starts_at: datetime,
        ends_at: datetime,
    ) -> None:
        await make_appointment(psychologist, record, starts_at=starts_at, ends_at=ends_at)

        response = await api.post(TIME_OFF, json=BODY)

        assert response.status_code == 409

    async def test_a_colleagues_appointment_does_not_block(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_client_record: ClientRecordFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        theirs = await make_client_record(therapist)
        await make_appointment(therapist, theirs, starts_at=on_tuesday(10))

        response = await api.post(TIME_OFF, json=BODY)

        assert response.status_code == 201

    async def test_moving_onto_an_appointment_conflicts_and_changes_nothing(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        period = await make_time_off(psychologist, starts_at=on_tuesday(7), ends_at=on_tuesday(8))
        await make_appointment(psychologist, record, starts_at=on_tuesday(9))

        response = await api.patch(
            f"{TIME_OFF}/{period.id}", json={"ends_at": "2026-12-08T09:30:00Z"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "TIME_OFF_CONFLICT"
        assert response.json()["error"]["details"] == {"appointment_count": 1}
        loaded = await load_time_off(db_session, period.id)
        assert loaded is not None
        assert loaded.ends_at == on_tuesday(8)
        assert await audit_entries(db_session, AuditAction.TIME_OFF_UPDATED) == []

    async def test_shrinking_away_from_an_appointment_is_allowed(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        make_time_off: TimeOffFactory,
    ) -> None:
        period = await make_time_off(psychologist, starts_at=on_tuesday(9), ends_at=on_tuesday(12))
        await make_appointment(psychologist, record, starts_at=on_tuesday(11))

        response = await api.patch(
            f"{TIME_OFF}/{period.id}", json={"ends_at": "2026-12-08T10:00:00Z"}
        )

        assert response.status_code == 200

    async def test_changing_only_the_reason_of_an_overlapping_period_is_allowed(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        make_time_off: TimeOffFactory,
    ) -> None:
        period = await make_time_off(psychologist, starts_at=on_tuesday(9), ends_at=on_tuesday(12))
        await make_appointment(psychologist, record, starts_at=on_tuesday(10))

        response = await api.patch(f"{TIME_OFF}/{period.id}", json={"reason": "Flu"})

        assert response.status_code == 200
        assert response.json()["reason"] == "Flu"

    async def test_sending_the_same_times_is_not_a_move(
        self,
        api: httpx.AsyncClient,
        record: Client,
        psychologist: User,
        make_appointment: AppointmentFactory,
        make_time_off: TimeOffFactory,
    ) -> None:
        period = await make_time_off(psychologist, starts_at=on_tuesday(9), ends_at=on_tuesday(12))
        await make_appointment(psychologist, record, starts_at=on_tuesday(10))

        response = await api.patch(
            f"{TIME_OFF}/{period.id}", json={"starts_at": "2026-12-08T12:00:00+03:00"}
        )

        assert response.status_code == 200

    async def test_a_period_moving_off_itself_does_not_conflict_with_itself(
        self, api: httpx.AsyncClient, psychologist: User, make_time_off: TimeOffFactory
    ) -> None:
        period = await make_time_off(psychologist, starts_at=on_tuesday(9), ends_at=on_tuesday(11))

        response = await api.patch(
            f"{TIME_OFF}/{period.id}", json={"starts_at": "2026-12-08T10:00:00Z"}
        )

        assert response.status_code == 200


class TestList:
    @pytest.fixture
    async def periods(
        self, psychologist: User, make_time_off: TimeOffFactory
    ) -> tuple[TimeOff, TimeOff, TimeOff]:
        """Three periods on one day, created out of order."""
        late = await make_time_off(psychologist, starts_at=on_tuesday(14), ends_at=on_tuesday(15))
        early = await make_time_off(psychologist, starts_at=on_tuesday(10), ends_at=on_tuesday(11))
        middle = await make_time_off(psychologist, starts_at=on_tuesday(12), ends_at=on_tuesday(13))
        return early, middle, late

    @staticmethod
    def ids(response: httpx.Response) -> list[str]:
        return [item["id"] for item in response.json()["items"]]

    async def test_periods_are_ordered_by_start(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        response = await api.get(TIME_OFF)

        assert response.status_code == 200
        assert self.ids(response) == [str(period.id) for period in periods]
        assert response.json()["total"] == 3

    async def test_a_colleagues_periods_are_not_listed(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist)

        response = await api.get(TIME_OFF)

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def test_results_are_paginated(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        response = await api.get(TIME_OFF, params={"limit": 1, "offset": 2})

        assert self.ids(response) == [str(periods[2].id)]
        body = response.json()
        assert (body["total"], body["limit"], body["offset"]) == (3, 1, 2)

    async def test_from_selects_periods_that_end_after_it(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        response = await api.get(TIME_OFF, params={"from": "2026-12-08T11:00:00Z"})

        assert self.ids(response) == [str(periods[1].id), str(periods[2].id)]
        assert response.json()["total"] == 2

    async def test_to_selects_periods_that_start_before_it(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        response = await api.get(TIME_OFF, params={"to": "2026-12-08T12:00:00Z"})

        assert self.ids(response) == [str(periods[0].id)]
        assert response.json()["total"] == 1

    async def test_both_filters_select_every_overlapping_period(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        response = await api.get(
            TIME_OFF,
            params={"from": "2026-12-08T10:30:00Z", "to": "2026-12-08T12:30:00Z"},
        )

        assert self.ids(response) == [str(periods[0].id), str(periods[1].id)]

    async def test_filters_may_use_any_offset(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        response = await api.get(TIME_OFF, params={"from": "2026-12-08T14:00:00+03:00"})

        assert self.ids(response) == [str(periods[1].id), str(periods[2].id)]

    async def test_a_single_instant_selects_the_period_around_it(
        self, api: httpx.AsyncClient, periods: tuple[TimeOff, TimeOff, TimeOff]
    ) -> None:
        instant = "2026-12-08T12:30:00Z"

        response = await api.get(TIME_OFF, params={"from": instant, "to": instant})

        assert self.ids(response) == [str(periods[1].id)]

    @pytest.mark.parametrize("name", ["from", "to"])
    @pytest.mark.parametrize("value", ["2026-12-08T10:00:00", "soon", "0001-01-01T00:00:00+03:00"])
    async def test_invalid_filters_are_rejected(
        self, api: httpx.AsyncClient, psychologist: User, name: str, value: str
    ) -> None:
        response = await api.get(TIME_OFF, params={name: value})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_reversed_filters_return_date_range_invalid(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.get(
            TIME_OFF,
            params={"from": "2026-12-09T00:00:00Z", "to": "2026-12-08T00:00:00Z"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "DATE_RANGE_INVALID"

    async def test_psychologist_may_name_themselves(
        self, api: httpx.AsyncClient, psychologist: User, make_time_off: TimeOffFactory
    ) -> None:
        await make_time_off(psychologist)

        response = await api.get(TIME_OFF, params={"psychologist_id": str(psychologist.id)})

        assert response.json()["total"] == 1

    async def test_naming_a_colleague_returns_404(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist, reason="Private")

        response = await api.get(TIME_OFF, params={"psychologist_id": str(therapist.id)})

        assert response.status_code == 404
        assert "Private" not in response.text

    async def test_admin_must_name_a_psychologist(
        self, api: httpx.AsyncClient, admin: User
    ) -> None:
        response = await api.get(TIME_OFF)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PSYCHOLOGIST_ID_REQUIRED"

    async def test_admin_reads_the_periods_of_the_psychologist_named(
        self,
        api: httpx.AsyncClient,
        admin: User,
        therapist: User,
        make_time_off: TimeOffFactory,
    ) -> None:
        period = await make_time_off(therapist)

        response = await api.get(TIME_OFF, params={"psychologist_id": str(therapist.id)})

        assert response.status_code == 200
        assert self.ids(response) == [str(period.id)]

    async def test_admin_naming_an_unknown_psychologist_gets_404(
        self, api: httpx.AsyncClient, admin: User
    ) -> None:
        response = await api.get(TIME_OFF, params={"psychologist_id": str(UNKNOWN_ID)})

        assert response.status_code == 404


class TestUpdate:
    async def test_only_the_fields_sent_change(
        self, api: httpx.AsyncClient, psychologist: User, make_time_off: TimeOffFactory
    ) -> None:
        period = await make_time_off(psychologist, reason="Conference")

        response = await api.patch(
            f"{TIME_OFF}/{period.id}", json={"ends_at": "2026-12-08T12:00:00Z"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["starts_at"] == "2026-12-08T10:00:00Z"
        assert body["ends_at"] == "2026-12-08T12:00:00Z"
        assert body["reason"] == "Conference"

    async def test_the_reason_can_be_cleared(
        self, api: httpx.AsyncClient, psychologist: User, make_time_off: TimeOffFactory
    ) -> None:
        period = await make_time_off(psychologist, reason="Conference")

        response = await api.patch(f"{TIME_OFF}/{period.id}", json={"reason": None})

        assert response.status_code == 200
        assert response.json()["reason"] is None

    @pytest.mark.parametrize("field", ["starts_at", "ends_at"])
    async def test_explicit_null_is_rejected_for_the_times(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_time_off: TimeOffFactory,
        field: str,
    ) -> None:
        period = await make_time_off(psychologist)

        response = await api.patch(f"{TIME_OFF}/{period.id}", json={field: None})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["fields"] == [
            {"field": f"body.{field}", "code": "null_not_allowed"}
        ]

    @pytest.mark.parametrize(
        "change",
        [{"starts_at": "2026-12-08T10:00:00"}, {"reason": "a" * 201}, {"reason": ""}],
    )
    async def test_invalid_values_are_rejected(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_time_off: TimeOffFactory,
        change: dict[str, Any],
    ) -> None:
        period = await make_time_off(psychologist)

        response = await api.patch(f"{TIME_OFF}/{period.id}", json=change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize(
        "change",
        [
            {"starts_at": "2026-12-08T11:00:00Z"},
            {"starts_at": "2026-12-08T12:00:00Z"},
            {"ends_at": "2026-12-08T10:00:00Z"},
            {"ends_at": "2026-12-08T09:00:00Z"},
            {"starts_at": "2026-12-08T12:00:00Z", "ends_at": "2026-12-08T11:30:00Z"},
        ],
    )
    async def test_the_merged_period_must_move_forward(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
        change: dict[str, Any],
    ) -> None:
        period = await make_time_off(psychologist)

        response = await api.patch(f"{TIME_OFF}/{period.id}", json=change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_TIME_RANGE"
        loaded = await load_time_off(db_session, period.id)
        assert loaded is not None
        assert (loaded.starts_at, loaded.ends_at) == (PERIOD_START, PERIOD_END)
        assert await audit_entries(db_session, AuditAction.TIME_OFF_UPDATED) == []

    async def test_a_period_may_move_past_its_old_end_in_one_request(
        self, api: httpx.AsyncClient, psychologist: User, make_time_off: TimeOffFactory
    ) -> None:
        period = await make_time_off(psychologist)

        response = await api.patch(
            f"{TIME_OFF}/{period.id}",
            json={"starts_at": "2026-12-09T10:00:00Z", "ends_at": "2026-12-09T11:00:00Z"},
        )

        assert response.status_code == 200

    async def test_update_is_audited_with_field_names_only(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        period = await make_time_off(psychologist)

        await api.patch(
            f"{TIME_OFF}/{period.id}",
            json={"reason": "Surgery", "ends_at": "2026-12-08T12:00:00Z"},
        )

        [entry] = await audit_entries(db_session, AuditAction.TIME_OFF_UPDATED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "time_off"
        assert entry.entity_id == period.id
        assert entry.event_metadata == {"changed_fields": ["ends_at", "reason"]}

    @pytest.mark.parametrize("body", [{}, {"starts_at": "2026-12-08T13:00:00+03:00"}])
    async def test_patch_that_changes_nothing_is_not_audited(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
        body: dict[str, Any],
    ) -> None:
        period = await make_time_off(psychologist)

        response = await api.patch(f"{TIME_OFF}/{period.id}", json=body)

        assert response.status_code == 200
        assert await audit_entries(db_session, AuditAction.TIME_OFF_UPDATED) == []

    async def test_a_colleagues_period_returns_404_and_stays_unchanged(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        period = await make_time_off(therapist, reason="Theirs")

        response = await api.patch(f"{TIME_OFF}/{period.id}", json={"reason": "Mine"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        loaded = await load_time_off(db_session, period.id)
        assert loaded is not None
        assert loaded.reason == "Theirs"

    async def test_an_unknown_period_returns_404(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.patch(f"{TIME_OFF}/{UNKNOWN_ID}", json={"reason": "x"})

        assert response.status_code == 404


class TestDelete:
    async def test_the_period_is_gone_and_the_deletion_audited(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        period = await make_time_off(psychologist, reason="Conference")
        period_id = period.id

        response = await api.delete(f"{TIME_OFF}/{period_id}")

        assert response.status_code == 204
        assert await load_time_off(db_session, period_id) is None
        [entry] = await audit_entries(db_session, AuditAction.TIME_OFF_DELETED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "time_off"
        assert entry.entity_id == period_id
        assert entry.event_metadata == {}

    async def test_a_colleagues_period_returns_404_and_stays(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        period = await make_time_off(therapist)

        response = await api.delete(f"{TIME_OFF}/{period.id}")

        assert response.status_code == 404
        assert await load_time_off(db_session, period.id) is not None
        assert await audit_entries(db_session, AuditAction.TIME_OFF_DELETED) == []

    async def test_an_unknown_period_returns_404(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.delete(f"{TIME_OFF}/{UNKNOWN_ID}")

        assert response.status_code == 404
