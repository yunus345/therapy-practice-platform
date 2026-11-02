import uuid
from collections.abc import Awaitable, Callable
from datetime import date, time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.modules.auth.dependencies import api_routes, route_policies
from app.modules.scheduling.models import AvailabilityRule, TimeOff
from app.modules.scheduling.schemas import (
    CreateAvailabilityRuleRequest,
    CreateTimeOffRequest,
    UpdateAvailabilityRuleRequest,
    UpdateTimeOffRequest,
)
from app.modules.scheduling.service import SchedulingService, build_scheduling_service
from app.modules.users.actor import Actor, ForbiddenError
from app.modules.users.models import User, UserRole
from tests.conftest import (
    CSRF_HEADER,
    UNKNOWN_ID,
    ClientFactory,
    FakeClock,
    SettingsFactory,
    UserFactory,
)
from tests.scheduling.conftest import (
    PERIOD_END,
    PERIOD_START,
    RuleFactory,
    TimeOffFactory,
    logged_in,
)

RULE_BODY: dict[str, Any] = {
    "weekday": 1,
    "start_time": "09:00:00",
    "end_time": "17:00:00",
    "valid_from": "2026-12-01",
}
PERIOD_BODY: dict[str, Any] = {
    "starts_at": "2026-12-09T10:00:00Z",
    "ends_at": "2026-12-09T11:00:00Z",
}

NEW_RULE = CreateAvailabilityRuleRequest(
    weekday=1, start_time=time(9), end_time=time(17), valid_from=date(2026, 12, 1)
)
NEW_PERIOD = CreateTimeOffRequest(starts_at=PERIOD_START, ends_at=PERIOD_END)

READ_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/staff/availability-rules", None),
    ("GET", "/staff/time-off", None),
]
WRITE_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/staff/availability-rules", RULE_BODY),
    ("PATCH", "/staff/availability-rules/{rule}", {"start_time": "10:00:00"}),
    ("DELETE", "/staff/availability-rules/{rule}", None),
    ("POST", "/staff/time-off", PERIOD_BODY),
    ("PATCH", "/staff/time-off/{period}", {"reason": "Changed"}),
    ("DELETE", "/staff/time-off/{period}", None),
]
ALL_ROUTES = READ_ROUTES + WRITE_ROUTES

read_routes = pytest.mark.parametrize(("method", "path", "body"), READ_ROUTES)
write_routes = pytest.mark.parametrize(("method", "path", "body"), WRITE_ROUTES)
all_routes = pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)


@pytest.fixture
async def rule(therapist: User, make_rule: RuleFactory) -> AvailabilityRule:
    return await make_rule(therapist)


@pytest.fixture
async def period(therapist: User, make_time_off: TimeOffFactory) -> TimeOff:
    return await make_time_off(therapist, reason="Original")


@all_routes
async def test_psychologist_can_call_every_route_on_their_schedule(
    therapist_api: httpx.AsyncClient,
    rule: AvailabilityRule,
    period: TimeOff,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    url = path.format(rule=rule.id, period=period.id)

    response = await therapist_api.request(method, url, json=body)

    assert response.status_code in {200, 201, 204}


@read_routes
async def test_admin_can_read_a_schedule_by_naming_the_psychologist(
    new_client: ClientFactory,
    make_user: UserFactory,
    therapist: User,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    client = await logged_in(new_client, make_user, UserRole.ADMIN)

    response = await client.request(
        method, path, params={"psychologist_id": str(therapist.id)}, json=body
    )

    assert response.status_code == 200


@write_routes
async def test_admin_gets_403_on_writes_and_changes_nothing(
    new_client: ClientFactory,
    make_user: UserFactory,
    rule: AvailabilityRule,
    period: TimeOff,
    db_session: AsyncSession,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    client = await logged_in(new_client, make_user, UserRole.ADMIN)
    url = path.format(rule=rule.id, period=period.id)

    response = await client.request(method, url, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    loaded_rule = await db_session.get(AvailabilityRule, rule.id, populate_existing=True)
    loaded_period = await db_session.get(TimeOff, period.id, populate_existing=True)
    assert loaded_rule is not None
    assert loaded_rule.start_time == time(9)
    assert loaded_period is not None
    assert loaded_period.reason == "Original"


@all_routes
async def test_clients_get_403(
    new_client: ClientFactory,
    make_user: UserFactory,
    rule: AvailabilityRule,
    period: TimeOff,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    client = await logged_in(new_client, make_user, UserRole.CLIENT)
    url = path.format(rule=rule.id, period=period.id)

    response = await client.request(method, url, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@all_routes
async def test_anonymous_callers_get_401(
    api: httpx.AsyncClient,
    rule: AvailabilityRule,
    period: TimeOff,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    url = path.format(rule=rule.id, period=period.id)

    response = await api.request(method, url, json=body)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@write_routes
async def test_writes_require_a_valid_csrf_token(
    therapist_api: httpx.AsyncClient,
    rule: AvailabilityRule,
    period: TimeOff,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    url = path.format(rule=rule.id, period=period.id)

    response = await therapist_api.request(method, url, json=body, headers={CSRF_HEADER: "forged"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


class TestWhenTwoFactorIsMandatory:
    @pytest.fixture
    def api_settings(self) -> dict[str, Any]:
        return {"require_staff_2fa": True}

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.PSYCHOLOGIST])
    @pytest.mark.parametrize("path", ["/staff/availability-rules", "/staff/time-off"])
    async def test_staff_without_two_factor_are_blocked(
        self, new_client: ClientFactory, make_user: UserFactory, role: UserRole, path: str
    ) -> None:
        client = await logged_in(new_client, make_user, role)

        response = await client.get(path)

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "TWO_FACTOR_SETUP_REQUIRED"


class TestSchedulingServiceWithoutARouter:
    """The future agent calls services directly, so each one must authorize by itself."""

    @pytest.fixture
    def service(
        self, db_session: AsyncSession, make_settings: SettingsFactory, clock: FakeClock
    ) -> SchedulingService:
        return build_scheduling_service(db_session, make_settings(), clock)

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
    @pytest.mark.parametrize(
        "write",
        [
            lambda service, actor: service.create_rule(actor, NEW_RULE, ip_address=None),
            lambda service, actor: service.update_rule(
                actor, UNKNOWN_ID, UpdateAvailabilityRuleRequest(weekday=2), ip_address=None
            ),
            lambda service, actor: service.delete_rule(actor, UNKNOWN_ID, ip_address=None),
            lambda service, actor: service.create_time_off(actor, NEW_PERIOD, ip_address=None),
            lambda service, actor: service.update_time_off(
                actor, UNKNOWN_ID, UpdateTimeOffRequest(reason="x"), ip_address=None
            ),
            lambda service, actor: service.delete_time_off(actor, UNKNOWN_ID, ip_address=None),
        ],
        ids=[
            "create_rule",
            "update_rule",
            "delete_rule",
            "create_time_off",
            "update_time_off",
            "delete_time_off",
        ],
    )
    async def test_only_psychologists_may_write(
        self,
        service: SchedulingService,
        write: Callable[[SchedulingService, Actor], Awaitable[Any]],
        role: UserRole,
    ) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=role)

        with pytest.raises(ForbiddenError):
            await write(service, actor)

    @pytest.mark.parametrize(
        "read",
        [
            lambda service, actor: service.list_rules(actor, None, PageParams()),
            lambda service, actor: service.list_time_off(actor, None, PageParams()),
        ],
        ids=["rules", "time_off"],
    )
    async def test_clients_may_not_read(
        self,
        service: SchedulingService,
        read: Callable[[SchedulingService, Actor], Awaitable[Any]],
    ) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=UserRole.CLIENT)

        with pytest.raises(ForbiddenError):
            await read(service, actor)


async def test_routes_declare_the_roles_that_may_call_them(api_app: FastAPI) -> None:
    declared = {
        (method, path): route_policies(route.dependant)
        for path, route in api_routes(api_app)
        for method in route.methods or ()
        if path.startswith(("/api/v1/staff/availability-rules", "/api/v1/staff/time-off"))
    }

    assert set(declared) == {
        (method, "/api/v1" + path.format(rule="{rule_id}", period="{time_off_id}"))
        for method, path, _ in ALL_ROUTES
    }
    for (method, _), policies in declared.items():
        expected = "roles:admin,psychologist" if method == "GET" else "roles:psychologist"
        assert expected in policies


async def test_routes_appear_in_the_openapi_document(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert set(paths["/api/v1/staff/availability-rules"]) == {"get", "post"}
    assert set(paths["/api/v1/staff/availability-rules/{rule_id}"]) == {"patch", "delete"}
    assert set(paths["/api/v1/staff/time-off"]) == {"get", "post"}
    assert set(paths["/api/v1/staff/time-off/{time_off_id}"]) == {"patch", "delete"}
    assert set(paths["/api/v1/public/psychologists/{slug}"]) == {"get"}
    assert set(paths["/api/v1/public/psychologists/{slug}/slots"]) == {"get"}


async def test_slot_times_are_documented_as_datetimes(client: httpx.AsyncClient) -> None:
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]

    properties = schemas["SlotResponse"]["properties"]
    assert properties["starts_at"]["format"] == "date-time"
    assert properties["ends_at"]["format"] == "date-time"
