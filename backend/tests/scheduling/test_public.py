from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import AppointmentStatus, CancellationReason
from app.modules.scheduling.repository import SchedulingRepository
from app.modules.users.models import PsychologistProfile, User, UserRole
from tests.conftest import (
    UNKNOWN_ID,
    AppointmentFactory,
    ClientRecordFactory,
    FakeClock,
    ServiceAssigner,
    ServiceFactory,
    UserFactory,
)
from tests.scheduling.conftest import (
    NOW,
    TUESDAY,
    RuleFactory,
    TimeOffFactory,
    load_profile,
    local,
)

pytestmark = pytest.mark.usefixtures("fixed_time")


@pytest.fixture
async def record(therapist: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(
        therapist,
        first_name="Zeynep",
        last_name="Yilmaz",
        email="zeynep@example.com",
        phone="+90 532 987 65 43",
    )


async def get_slots(
    api: httpx.AsyncClient,
    profile: PsychologistProfile,
    service: Service,
    from_date: date = TUESDAY,
    to_date: date | None = None,
) -> httpx.Response:
    return await api.get(
        f"/public/psychologists/{profile.slug}/slots",
        params={
            "service_id": str(service.id),
            "from": from_date.isoformat(),
            "to": (to_date or from_date).isoformat(),
        },
    )


def local_times(response: httpx.Response) -> list[str]:
    assert response.status_code == 200
    return [slot["starts_at"][11:16] for slot in response.json()["slots"]]


class TestPublicProfile:
    async def test_it_shows_exactly_the_public_fields(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
    ) -> None:
        service.description = "One to one."
        profile.title = "Clinical Psychologist"
        profile.bio = "I work with adults."
        await db_session.commit()

        response = await api.get(f"/public/psychologists/{profile.slug}")

        assert response.status_code == 200
        assert response.json() == {
            "first_name": "A",
            "last_name": "B",
            "title": "Clinical Psychologist",
            "slug": profile.slug,
            "bio": "I work with adults.",
            "timezone": "Europe/Istanbul",
            "cancellation_window_hours": 24,
            "services": [
                {
                    "id": str(service.id),
                    "name": "Individual Therapy",
                    "description": "One to one.",
                    "duration_minutes": 50,
                    "price_amount": "1500.00",
                    "currency": "TRY",
                    "delivery_mode": "in_person",
                }
            ],
        }

    async def test_it_reveals_no_contact_details_or_booking_internals(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        db_session: AsyncSession,
    ) -> None:
        profile.phone = "+90 532 111 22 33"
        await db_session.commit()

        response = await api.get(f"/public/psychologists/{profile.slug}")

        assert therapist.email not in response.text
        assert str(therapist.id) not in response.text
        assert profile.phone not in response.text

    async def test_services_show_the_effective_price(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
        assign_service: ServiceAssigner,
    ) -> None:
        custom = await make_service(name="Custom", price_amount=Decimal("1500.00"))
        free = await make_service(name="Free", price_amount=Decimal("1500.00"))
        listed = await make_service(name="Listed", price_amount=Decimal("900.00"))
        await assign_service(therapist, custom, Decimal("1200.00"))
        await assign_service(therapist, free, Decimal("0.00"))
        await assign_service(therapist, listed)

        response = await api.get(f"/public/psychologists/{profile.slug}")

        prices = {item["name"]: item["price_amount"] for item in response.json()["services"]}
        assert prices == {"Custom": "1200.00", "Free": "0.00", "Listed": "900.00"}

    async def test_services_that_are_inactive_or_not_assigned_are_left_out(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
        assign_service: ServiceAssigner,
    ) -> None:
        await assign_service(therapist, await make_service(is_active=False))
        await make_service()

        response = await api.get(f"/public/psychologists/{profile.slug}")

        assert response.json()["services"] == []

    async def test_a_deactivated_psychologist_looks_exactly_like_an_unknown_one(
        self,
        api: httpx.AsyncClient,
        make_user: UserFactory,
        db_session: AsyncSession,
    ) -> None:
        user = await make_user(UserRole.PSYCHOLOGIST, is_active=False)
        slug = (await load_profile(db_session, user)).slug

        deactivated = await api.get(f"/public/psychologists/{slug}")
        unknown = await api.get("/public/psychologists/no-such-psychologist")

        assert deactivated.status_code == unknown.status_code == 404
        assert deactivated.json() == unknown.json()
        assert unknown.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.parametrize("slug", ["a%00b", "a" * 101])
    async def test_a_slug_that_cannot_exist_never_reaches_the_database(
        self, api: httpx.AsyncClient, slug: str
    ) -> None:
        response = await api.get(f"/public/psychologists/{slug}")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_it_needs_no_session_or_csrf_cookie(
        self, api: httpx.AsyncClient, profile: PsychologistProfile
    ) -> None:
        api.cookies.clear()

        response = await api.get(f"/public/psychologists/{profile.slug}")

        assert response.status_code == 200


class TestSlots:
    async def test_times_are_rendered_in_the_timezone_of_the_psychologist(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist, start_time=time(9), end_time=time(10))

        response = await get_slots(api, profile, service)

        assert response.status_code == 200
        assert response.json() == {
            "timezone": "Europe/Istanbul",
            "slots": [
                {
                    "starts_at": "2026-12-08T09:00:00+03:00",
                    "ends_at": "2026-12-08T09:50:00+03:00",
                }
            ],
        }

    async def test_a_zero_offset_is_still_written_as_an_offset(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.timezone = "Europe/London"
        await db_session.commit()
        await make_rule(therapist, start_time=time(9), end_time=time(10))

        response = await get_slots(api, profile, service)

        assert response.json()["slots"][0]["starts_at"] == "2026-12-08T09:00:00+00:00"

    async def test_a_session_across_a_dst_change_lasts_its_real_duration(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
        assign_service: ServiceAssigner,
        make_rule: RuleFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.timezone = "America/New_York"
        profile.max_booking_advance_days = 365
        await db_session.commit()
        service = await make_service(duration_minutes=60)
        await assign_service(therapist, service)
        await make_rule(therapist, weekday=6, start_time=time(1), end_time=time(2))
        await make_rule(therapist, weekday=6, start_time=time(3), end_time=time(4))

        response = await get_slots(api, profile, service, date(2027, 3, 14))

        assert response.json()["timezone"] == "America/New_York"
        assert response.json()["slots"][0] == {
            "starts_at": "2027-03-14T01:00:00-05:00",
            "ends_at": "2027-03-14T03:00:00-04:00",
        }

    async def test_dates_are_inclusive_local_dates(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist, weekday=1, start_time=time(0), end_time=time(0, 50))
        await make_rule(therapist, weekday=2, start_time=time(23), end_time=time(23, 50))

        response = await get_slots(api, profile, service, TUESDAY, TUESDAY + timedelta(days=1))

        assert [slot["starts_at"] for slot in response.json()["slots"]] == [
            "2026-12-08T00:00:00+03:00",
            "2026-12-09T23:00:00+03:00",
        ]

    async def test_no_rules_means_no_slots(
        self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await get_slots(api, profile, service)

        assert response.json() == {"timezone": "Europe/Istanbul", "slots": []}

    async def test_only_rules_valid_on_the_date_count(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist, valid_until=date(2026, 12, 7))
        await make_rule(therapist, start_time=time(9), end_time=time(10), valid_from=TUESDAY)

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:00"]

    async def test_a_rule_ending_on_the_date_still_counts(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist, start_time=time(9), end_time=time(10), valid_until=TUESDAY)

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:00"]

    async def test_a_colleagues_rules_do_not_count(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        make_user: UserFactory,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(await make_user(UserRole.PSYCHOLOGIST))

        response = await get_slots(api, profile, service)

        assert local_times(response) == []

    async def test_the_step_comes_from_the_default_granularity(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
    ) -> None:
        await make_rule(therapist, start_time=time(9), end_time=time(10, 20))

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:00", "09:15", "09:30"]

    class TestWithAnotherGranularity:
        @pytest.fixture
        def api_settings(self) -> dict[str, Any]:
            return {"slot_granularity_minutes": 10}

        async def test_the_step_follows_the_setting(
            self,
            api: httpx.AsyncClient,
            therapist: User,
            profile: PsychologistProfile,
            service: Service,
            make_rule: RuleFactory,
        ) -> None:
            await make_rule(therapist, start_time=time(9), end_time=time(10, 10))

            response = await get_slots(api, profile, service)

            assert local_times(response) == ["09:00", "09:10", "09:20"]


class TestBookingLimits:
    @pytest.fixture(autouse=True)
    async def rule(self, therapist: User, make_rule: RuleFactory) -> None:
        await make_rule(therapist, start_time=time(9), end_time=time(10, 20))

    async def test_a_slot_exactly_at_the_notice_limit_is_offered(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        clock: FakeClock,
    ) -> None:
        clock.now = local(9) - timedelta(hours=24)

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:00", "09:15", "09:30"]

    async def test_a_slot_a_second_inside_the_notice_limit_is_not(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        clock: FakeClock,
    ) -> None:
        clock.now = local(9) - timedelta(hours=24) + timedelta(seconds=1)

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:15", "09:30"]

    async def test_a_slot_exactly_at_the_advance_limit_is_offered(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        clock: FakeClock,
    ) -> None:
        clock.now = local(9, 15) - timedelta(days=60)

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:00", "09:15"]

    async def test_notice_and_advance_come_from_the_profile(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        clock: FakeClock,
        db_session: AsyncSession,
    ) -> None:
        profile.min_booking_notice_hours = 1
        profile.max_booking_advance_days = 1
        await db_session.commit()
        clock.now = local(8, 15)

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:15", "09:30"]

    async def test_past_dates_have_no_slots(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
    ) -> None:
        response = await get_slots(api, profile, service, date(2026, 11, 3))

        assert local_times(response) == []

    @pytest.mark.parametrize("timezone", ["Europe/Istanbul", "America/New_York"])
    @pytest.mark.parametrize(
        ("from_date", "to_date"),
        [
            (date.min, date.min + timedelta(days=1)),
            (date.max - timedelta(days=1), date.max),
        ],
        ids=["first_dates", "last_dates"],
    )
    async def test_the_far_ends_of_the_calendar_are_empty_rather_than_an_error(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        db_session: AsyncSession,
        timezone: str,
        from_date: date,
        to_date: date,
    ) -> None:
        profile.timezone = timezone
        await db_session.commit()

        response = await get_slots(api, profile, service, from_date, to_date)

        assert local_times(response) == []


class TestBusyTimes:
    @pytest.fixture(autouse=True)
    async def rule(self, therapist: User, make_rule: RuleFactory) -> None:
        await make_rule(therapist, start_time=time(9), end_time=time(11))

    async def test_an_appointment_removes_the_slots_around_it(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(
            therapist, record, starts_at=local(9, 30), ends_at=local(9, 45), service_id=service.id
        )

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:45", "10:00"]

    async def test_a_requested_appointment_holds_its_slot_too(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_appointment(
            therapist,
            record,
            starts_at=local(9),
            ends_at=local(11),
            status=AppointmentStatus.REQUESTED,
        )

        response = await get_slots(api, profile, service)

        assert local_times(response) == []

    @pytest.mark.parametrize(
        "fields",
        [
            {"status": AppointmentStatus.COMPLETED},
            {"status": AppointmentStatus.NO_SHOW},
            {
                "status": AppointmentStatus.CANCELLED,
                "cancelled_at": NOW,
                "cancellation_reason": CancellationReason.CLIENT_REQUEST,
            },
        ],
        ids=["completed", "no_show", "cancelled"],
    )
    async def test_a_closed_appointment_frees_its_slot(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
        fields: dict[str, Any],
    ) -> None:
        await make_appointment(therapist, record, starts_at=local(9), ends_at=local(11), **fields)

        response = await get_slots(api, profile, service)

        assert "09:00" in local_times(response)

    async def test_a_colleagues_appointment_changes_nothing(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        make_user: UserFactory,
        make_client_record: ClientRecordFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        colleague = await make_user(UserRole.PSYCHOLOGIST)
        theirs = await make_client_record(colleague)
        await make_appointment(colleague, theirs, starts_at=local(9), ends_at=local(11))

        response = await get_slots(api, profile, service)

        assert "09:00" in local_times(response)

    async def test_time_off_removes_the_slots_it_overlaps(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        service: Service,
        make_time_off: TimeOffFactory,
    ) -> None:
        await make_time_off(therapist, starts_at=local(9, 30), ends_at=local(10))

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["10:00"]

    async def test_a_colleagues_time_off_changes_nothing(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        make_user: UserFactory,
        make_time_off: TimeOffFactory,
    ) -> None:
        colleague = await make_user(UserRole.PSYCHOLOGIST)
        await make_time_off(colleague, starts_at=local(9), ends_at=local(11))

        response = await get_slots(api, profile, service)

        assert "09:00" in local_times(response)

    async def test_the_buffer_comes_from_the_profile_and_skips_time_off(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_appointment: AppointmentFactory,
        make_time_off: TimeOffFactory,
        db_session: AsyncSession,
    ) -> None:
        profile.buffer_minutes = 15
        await db_session.commit()
        await make_appointment(therapist, record, starts_at=local(8), ends_at=local(8, 50))
        await make_time_off(therapist, starts_at=local(10, 5), ends_at=local(11))

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["09:15"]


class TestBufferAcrossTheRequestedRange:
    @pytest.fixture(autouse=True)
    async def buffer(self, profile: PsychologistProfile, db_session: AsyncSession) -> None:
        profile.buffer_minutes = 30
        await db_session.commit()

    async def test_an_appointment_the_evening_before_blocks_the_first_slot(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_rule(therapist, start_time=time(0), end_time=time(1, 20))
        await make_appointment(
            therapist, record, starts_at=local(23, day=date(2026, 12, 7)), ends_at=local(0)
        )

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["00:30"]

    async def test_an_appointment_after_midnight_blocks_the_last_slot(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
        make_rule: RuleFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_rule(therapist, start_time=time(22, 30), end_time=time(23, 40))
        wednesday = TUESDAY + timedelta(days=1)
        await make_appointment(
            therapist,
            record,
            starts_at=local(0, 4, day=wednesday),
            ends_at=local(1, day=wednesday),
        )

        response = await get_slots(api, profile, service)

        assert local_times(response) == ["22:30"]


class TestRequestValidation:
    async def test_the_end_may_equal_the_start(
        self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await get_slots(api, profile, service, TUESDAY, TUESDAY)

        assert response.status_code == 200

    async def test_the_end_may_not_precede_the_start(
        self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await get_slots(api, profile, service, TUESDAY, TUESDAY - timedelta(days=1))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "DATE_RANGE_INVALID"

    async def test_the_range_may_cover_exactly_the_longest_length(
        self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await get_slots(api, profile, service, TUESDAY, TUESDAY + timedelta(days=30))

        assert response.status_code == 200

    async def test_a_range_one_day_longer_is_rejected(
        self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
    ) -> None:
        response = await get_slots(api, profile, service, TUESDAY, TUESDAY + timedelta(days=31))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "DATE_RANGE_INVALID"

    class TestWithAShorterLimit:
        @pytest.fixture
        def api_settings(self) -> dict[str, Any]:
            return {"slot_max_range_days": 2}

        async def test_the_limit_follows_the_setting(
            self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
        ) -> None:
            longest = await get_slots(api, profile, service, TUESDAY, TUESDAY + timedelta(days=1))
            longer = await get_slots(api, profile, service, TUESDAY, TUESDAY + timedelta(days=2))

            assert longest.status_code == 200
            assert longer.status_code == 422

    @pytest.mark.parametrize("name", ["service_id", "from", "to"])
    async def test_every_parameter_is_required(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        name: str,
    ) -> None:
        params = {"service_id": str(service.id), "from": "2026-12-08", "to": "2026-12-08"}
        del params[name]

        response = await api.get(f"/public/psychologists/{profile.slug}/slots", params=params)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize(
        "change",
        [{"service_id": "not-a-uuid"}, {"from": "tomorrow"}, {"to": "2026-13-01"}],
    )
    async def test_malformed_parameters_are_rejected(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        service: Service,
        change: dict[str, str],
    ) -> None:
        params = {"service_id": str(service.id), "from": "2026-12-08", "to": "2026-12-08"}

        response = await api.get(
            f"/public/psychologists/{profile.slug}/slots", params=params | change
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_an_unknown_service_returns_404(
        self, api: httpx.AsyncClient, profile: PsychologistProfile
    ) -> None:
        response = await api.get(
            f"/public/psychologists/{profile.slug}/slots",
            params={"service_id": str(UNKNOWN_ID), "from": "2026-12-08", "to": "2026-12-08"},
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_a_service_that_is_not_assigned_returns_404(
        self,
        api: httpx.AsyncClient,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
    ) -> None:
        response = await get_slots(api, profile, await make_service())

        assert response.status_code == 404

    async def test_a_deactivated_service_returns_404(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        profile: PsychologistProfile,
        make_service: ServiceFactory,
        assign_service: ServiceAssigner,
    ) -> None:
        service = await make_service(is_active=False)
        await assign_service(therapist, service)

        response = await get_slots(api, profile, service)

        assert response.status_code == 404

    async def test_a_deactivated_psychologist_looks_exactly_like_an_unknown_one(
        self,
        api: httpx.AsyncClient,
        make_user: UserFactory,
        service: Service,
        assign_service: ServiceAssigner,
        db_session: AsyncSession,
    ) -> None:
        user = await make_user(UserRole.PSYCHOLOGIST, is_active=False)
        await assign_service(user, service)
        slug = (await load_profile(db_session, user)).slug
        params = {"service_id": str(service.id), "from": "2026-12-08", "to": "2026-12-08"}

        deactivated = await api.get(f"/public/psychologists/{slug}/slots", params=params)
        unknown = await api.get("/public/psychologists/no-such-one/slots", params=params)

        assert deactivated.status_code == unknown.status_code == 404
        assert deactivated.json() == unknown.json()


class TestPrivacy:
    @pytest.fixture(autouse=True)
    async def busy_schedule(
        self,
        therapist: User,
        record: Client,
        service: Service,
        make_rule: RuleFactory,
        make_time_off: TimeOffFactory,
        make_appointment: AppointmentFactory,
    ) -> None:
        await make_rule(therapist, start_time=time(9), end_time=time(17))
        await make_appointment(therapist, record, starts_at=local(10), service_id=service.id)
        await make_time_off(
            therapist, starts_at=local(14), ends_at=local(15), reason="Private surgery"
        )

    @staticmethod
    def assert_private(response: httpx.Response, therapist: User, record: Client) -> None:
        assert response.status_code == 200
        text = response.text
        for secret in (
            record.first_name,
            record.last_name,
            record.email,
            record.phone,
            str(record.id),
            therapist.email,
            str(therapist.id),
            "Private surgery",
            "surgery",
            "reason",
            "client",
            "appointment",
            "buffer",
            "notice",
        ):
            assert secret and secret not in text

    async def test_the_profile_reveals_nothing_about_the_schedule(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
    ) -> None:
        response = await api.get(f"/public/psychologists/{profile.slug}")

        self.assert_private(response, therapist, record)

    async def test_slots_reveal_nothing_about_why_a_time_is_busy(
        self,
        api: httpx.AsyncClient,
        therapist: User,
        record: Client,
        profile: PsychologistProfile,
        service: Service,
    ) -> None:
        response = await get_slots(api, profile, service)

        self.assert_private(response, therapist, record)
        assert set(response.json()) == {"timezone", "slots"}
        assert all(set(slot) == {"starts_at", "ends_at"} for slot in response.json()["slots"])

    async def test_busy_data_is_read_as_bare_time_ranges(
        self, db_session: AsyncSession, therapist: User
    ) -> None:
        repository = SchedulingRepository(db_session)
        start, end = local(0), local(23)

        [appointment] = await repository.appointment_ranges(therapist.id, start, end)
        [time_off] = await repository.time_off_ranges(therapist.id, start, end)

        assert appointment._fields == ("starts_at", "ends_at")
        assert time_off._fields == ("starts_at", "ends_at")


class TestRateLimit:
    async def test_the_profile_allows_60_requests_a_minute(
        self, api: httpx.AsyncClient, profile: PsychologistProfile
    ) -> None:
        url = f"/public/psychologists/{profile.slug}"
        for _ in range(60):
            assert (await api.get(url)).status_code == 200

        response = await api.get(url)

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"
        assert "Retry-After" in response.headers

    async def test_slots_allow_60_requests_a_minute(
        self, api: httpx.AsyncClient, profile: PsychologistProfile, service: Service
    ) -> None:
        for _ in range(60):
            assert (await get_slots(api, profile, service)).status_code == 200

        response = await get_slots(api, profile, service)

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"

    async def test_unknown_slugs_count_too(self, api: httpx.AsyncClient) -> None:
        for _ in range(60):
            await api.get("/public/psychologists/guess")

        response = await api.get("/public/psychologists/guess")

        assert response.status_code == 429
