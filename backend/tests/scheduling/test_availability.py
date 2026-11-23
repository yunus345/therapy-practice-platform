import uuid
from datetime import date, time
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.clients.models import Client
from app.modules.scheduling.models import Appointment, AppointmentStatus, AvailabilityRule
from app.modules.users.models import User
from tests.conftest import (
    UNKNOWN_ID,
    AppointmentFactory,
    ClientRecordFactory,
    audit_entries,
)
from tests.scheduling.conftest import RuleFactory

RULES = "/staff/availability-rules"
BODY: dict[str, Any] = {
    "weekday": 1,
    "start_time": "09:00:00",
    "end_time": "17:00:00",
    "valid_from": "2026-12-01",
    "valid_until": "2027-06-30",
}
NOT_NULLABLE = ["weekday", "start_time", "end_time", "valid_from"]


async def load_rule(session: AsyncSession, rule_id: uuid.UUID | str) -> AvailabilityRule | None:
    return await session.get(AvailabilityRule, rule_id, populate_existing=True)


class TestCreate:
    async def test_psychologist_creates_a_rule_for_themselves(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        response = await api.post(RULES, json=BODY)

        assert response.status_code == 201
        body = response.json()
        assert body["psychologist_id"] == str(psychologist.id)
        assert {name: body[name] for name in BODY} == BODY
        rule = await load_rule(db_session, uuid.UUID(body["id"]))
        assert rule is not None
        assert rule.psychologist_id == psychologist.id
        assert (rule.start_time, rule.end_time) == (time(9), time(17))

    async def test_the_end_of_validity_is_optional(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        body = {name: value for name, value in BODY.items() if name != "valid_until"}

        response = await api.post(RULES, json=body)

        assert response.status_code == 201
        assert response.json()["valid_until"] is None

    async def test_a_rule_may_be_valid_for_a_single_day(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.post(RULES, json=BODY | {"valid_until": BODY["valid_from"]})

        assert response.status_code == 201

    @pytest.mark.parametrize("weekday", [0, 6])
    async def test_weekday_accepts_the_limits_of_its_range(
        self, api: httpx.AsyncClient, psychologist: User, weekday: int
    ) -> None:
        response = await api.post(RULES, json=BODY | {"weekday": weekday})

        assert response.status_code == 201

    async def test_a_field_for_the_owner_is_ignored(
        self, api: httpx.AsyncClient, psychologist: User, therapist: User
    ) -> None:
        response = await api.post(RULES, json=BODY | {"psychologist_id": str(therapist.id)})

        assert response.status_code == 201
        assert response.json()["psychologist_id"] == str(psychologist.id)

    @pytest.mark.parametrize(
        "change",
        [
            {"weekday": -1},
            {"weekday": 7},
            {"weekday": "monday"},
            {"start_time": "25:00"},
            {"start_time": "09:00:00+03:00"},
            {"start_time": "09:00:00Z"},
            {"end_time": "17:00:00+03:00"},
            {"end_time": "17:00:00Z"},
            {"valid_from": "soon"},
            {"valid_until": "later"},
        ],
    )
    async def test_invalid_values_are_rejected(
        self, api: httpx.AsyncClient, psychologist: User, change: dict[str, Any]
    ) -> None:
        response = await api.post(RULES, json=BODY | change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize("field", NOT_NULLABLE)
    async def test_required_fields_cannot_be_left_out(
        self, api: httpx.AsyncClient, psychologist: User, field: str
    ) -> None:
        body = {name: value for name, value in BODY.items() if name != field}

        response = await api.post(RULES, json=body)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize(
        "change",
        [
            {"start_time": "17:00:00"},
            {"start_time": "18:00:00"},
            {"valid_until": "2026-11-30"},
        ],
        ids=["same_time", "reversed_times", "reversed_dates"],
    )
    async def test_a_reversed_range_returns_invalid_time_range(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        db_session: AsyncSession,
        change: dict[str, Any],
    ) -> None:
        response = await api.post(RULES, json=BODY | change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_TIME_RANGE"
        assert await audit_entries(db_session, AuditAction.AVAILABILITY_CREATED) == []

    async def test_creation_is_audited(
        self, api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
    ) -> None:
        response = await api.post(RULES, json=BODY)

        [entry] = await audit_entries(db_session, AuditAction.AVAILABILITY_CREATED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "availability_rule"
        assert str(entry.entity_id) == response.json()["id"]
        assert str(entry.ip_address) == "127.0.0.1"
        assert entry.event_metadata == {}


class TestList:
    async def test_psychologist_sees_their_own_rules_in_schedule_order(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_rule: RuleFactory,
    ) -> None:
        late = await make_rule(psychologist, weekday=2, start_time=time(14))
        second = await make_rule(psychologist, weekday=1, start_time=time(13))
        first = await make_rule(psychologist, weekday=1, start_time=time(9))
        await make_rule(therapist)

        response = await api.get(RULES)

        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body["items"]] == [
            str(first.id),
            str(second.id),
            str(late.id),
        ]
        assert (body["total"], body["limit"], body["offset"]) == (3, 50, 0)

    async def test_results_are_paginated_and_total_counts_everything(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        rules = [await make_rule(psychologist, weekday=day) for day in range(3)]

        response = await api.get(RULES, params={"limit": 1, "offset": 1})

        body = response.json()
        assert [item["id"] for item in body["items"]] == [str(rules[1].id)]
        assert (body["total"], body["limit"], body["offset"]) == (3, 1, 1)

    async def test_no_rules_is_an_empty_page(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.get(RULES)

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def test_psychologist_may_name_themselves(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        await make_rule(psychologist)

        response = await api.get(RULES, params={"psychologist_id": str(psychologist.id)})

        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_naming_a_colleague_returns_404(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist)

        response = await api.get(RULES, params={"psychologist_id": str(therapist.id)})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_admin_must_name_a_psychologist(
        self, api: httpx.AsyncClient, admin: User
    ) -> None:
        response = await api.get(RULES)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PSYCHOLOGIST_ID_REQUIRED"

    async def test_admin_reads_the_rules_of_the_psychologist_named(
        self,
        api: httpx.AsyncClient,
        admin: User,
        therapist: User,
        make_rule: RuleFactory,
    ) -> None:
        rule = await make_rule(therapist)

        response = await api.get(RULES, params={"psychologist_id": str(therapist.id)})

        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [str(rule.id)]

    async def test_admin_naming_an_unknown_psychologist_gets_404(
        self, api: httpx.AsyncClient, admin: User
    ) -> None:
        response = await api.get(RULES, params={"psychologist_id": str(UNKNOWN_ID)})

        assert response.status_code == 404

    async def test_admin_naming_someone_without_a_profile_gets_404(
        self, api: httpx.AsyncClient, admin: User
    ) -> None:
        response = await api.get(RULES, params={"psychologist_id": str(admin.id)})

        assert response.status_code == 404


class TestUpdate:
    async def test_only_the_fields_sent_change(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        rule = await make_rule(psychologist, weekday=1, valid_until=date(2027, 1, 1))

        response = await api.patch(f"{RULES}/{rule.id}", json={"start_time": "10:30:00"})

        assert response.status_code == 200
        body = response.json()
        assert body["start_time"] == "10:30:00"
        assert body["end_time"] == "17:00:00"
        assert body["weekday"] == 1
        assert body["valid_until"] == "2027-01-01"

    async def test_every_field_can_change(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(psychologist)

        response = await api.patch(f"{RULES}/{rule.id}", json=BODY | {"weekday": 3})

        assert response.status_code == 200
        loaded = await load_rule(db_session, rule.id)
        assert loaded is not None
        assert (loaded.weekday, loaded.valid_from, loaded.valid_until) == (
            3,
            date(2026, 12, 1),
            date(2027, 6, 30),
        )

    async def test_the_end_of_validity_can_be_cleared(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        rule = await make_rule(psychologist, valid_until=date(2027, 1, 1))

        response = await api.patch(f"{RULES}/{rule.id}", json={"valid_until": None})

        assert response.status_code == 200
        assert response.json()["valid_until"] is None

    @pytest.mark.parametrize("field", NOT_NULLABLE)
    async def test_explicit_null_is_rejected_for_other_fields(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory, field: str
    ) -> None:
        rule = await make_rule(psychologist)

        response = await api.patch(f"{RULES}/{rule.id}", json={field: None})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["fields"] == [
            {"field": f"body.{field}", "code": "null_not_allowed"}
        ]

    @pytest.mark.parametrize(
        "change", [{"weekday": 7}, {"start_time": "09:00:00+03:00"}, {"end_time": "17:00:00Z"}]
    )
    async def test_invalid_values_are_rejected(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        change: dict[str, Any],
    ) -> None:
        rule = await make_rule(psychologist)

        response = await api.patch(f"{RULES}/{rule.id}", json=change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize(
        "change",
        [
            {"start_time": "17:00:00"},
            {"start_time": "18:00:00"},
            {"end_time": "09:00:00"},
            {"end_time": "08:00:00"},
            {"valid_until": "2019-12-31"},
            {"valid_from": "2027-01-02"},
            {"start_time": "12:00:00", "end_time": "11:00:00"},
        ],
    )
    async def test_the_merged_rule_must_still_be_a_valid_range(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
        change: dict[str, Any],
    ) -> None:
        rule = await make_rule(psychologist, valid_until=date(2027, 1, 1))

        response = await api.patch(f"{RULES}/{rule.id}", json=change)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_TIME_RANGE"
        loaded = await load_rule(db_session, rule.id)
        assert loaded is not None
        assert (loaded.start_time, loaded.end_time) == (time(9), time(17))
        assert await audit_entries(db_session, AuditAction.AVAILABILITY_UPDATED) == []

    async def test_a_range_may_move_past_its_old_end_in_one_request(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        rule = await make_rule(psychologist, start_time=time(9), end_time=time(12))

        response = await api.patch(
            f"{RULES}/{rule.id}", json={"start_time": "13:00:00", "end_time": "15:00:00"}
        )

        assert response.status_code == 200

    async def test_clearing_the_end_of_validity_lifts_the_range_check(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        rule = await make_rule(psychologist, valid_until=date(2027, 1, 1))

        response = await api.patch(
            f"{RULES}/{rule.id}", json={"valid_from": "2028-01-01", "valid_until": None}
        )

        assert response.status_code == 200

    async def test_update_is_audited_with_field_names(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(psychologist)

        await api.patch(f"{RULES}/{rule.id}", json={"weekday": 2, "start_time": "08:00:00"})

        [entry] = await audit_entries(db_session, AuditAction.AVAILABILITY_UPDATED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "availability_rule"
        assert entry.entity_id == rule.id
        assert entry.event_metadata == {"changed_fields": ["start_time", "weekday"]}

    @pytest.mark.parametrize("body", [{}, {"weekday": 1, "start_time": "09:00:00"}])
    async def test_patch_that_changes_nothing_is_not_audited(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
        body: dict[str, Any],
    ) -> None:
        rule = await make_rule(psychologist)

        response = await api.patch(f"{RULES}/{rule.id}", json=body)

        assert response.status_code == 200
        assert await audit_entries(db_session, AuditAction.AVAILABILITY_UPDATED) == []

    async def test_a_colleagues_rule_returns_404_and_stays_unchanged(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(therapist)

        response = await api.patch(f"{RULES}/{rule.id}", json={"weekday": 5})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        loaded = await load_rule(db_session, rule.id)
        assert loaded is not None
        assert loaded.weekday == 1

    async def test_an_unknown_rule_returns_404(
        self, api: httpx.AsyncClient, psychologist: User
    ) -> None:
        response = await api.patch(f"{RULES}/{UNKNOWN_ID}", json={"weekday": 5})

        assert response.status_code == 404


class TestDelete:
    async def test_the_rule_is_gone(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(psychologist)
        rule_id = rule.id

        response = await api.delete(f"{RULES}/{rule_id}")

        assert response.status_code == 204
        assert await load_rule(db_session, rule_id) is None

    async def test_deletion_is_audited(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(psychologist)
        rule_id = rule.id

        await api.delete(f"{RULES}/{rule_id}")

        [entry] = await audit_entries(db_session, AuditAction.AVAILABILITY_DELETED)
        assert entry.actor_user_id == psychologist.id
        assert entry.entity_type == "availability_rule"
        assert entry.entity_id == rule_id
        assert entry.event_metadata == {}

    async def test_deleting_twice_returns_404(
        self, api: httpx.AsyncClient, psychologist: User, make_rule: RuleFactory
    ) -> None:
        rule = await make_rule(psychologist)
        rule_id = rule.id
        await api.delete(f"{RULES}/{rule_id}")

        response = await api.delete(f"{RULES}/{rule_id}")

        assert response.status_code == 404

    async def test_a_colleagues_rule_returns_404_and_stays(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        therapist: User,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(therapist)

        response = await api.delete(f"{RULES}/{rule.id}")

        assert response.status_code == 404
        assert await load_rule(db_session, rule.id) is not None
        assert await audit_entries(db_session, AuditAction.AVAILABILITY_DELETED) == []

    async def test_existing_appointments_are_not_affected(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_rule: RuleFactory,
        make_client_record: ClientRecordFactory,
        make_appointment: AppointmentFactory,
        db_session: AsyncSession,
    ) -> None:
        rule = await make_rule(psychologist)
        record: Client = await make_client_record(psychologist)
        appointment = await make_appointment(psychologist, record)

        response = await api.delete(f"{RULES}/{rule.id}")

        assert response.status_code == 204
        loaded = await db_session.get(Appointment, appointment.id, populate_existing=True)
        assert loaded is not None
        assert loaded.status is AppointmentStatus.CONFIRMED
