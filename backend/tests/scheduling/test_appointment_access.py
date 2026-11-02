import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.modules.auth.dependencies import api_routes, route_policies
from app.modules.catalog.models import Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import Appointment, AppointmentStatus, AppointmentWhen
from app.modules.scheduling.schemas import (
    BookAppointmentRequest,
    ChangeAppointmentStatusRequest,
    CreateStaffAppointmentRequest,
    RescheduleAppointmentRequest,
)
from app.modules.scheduling.service import SchedulingService, build_scheduling_service
from app.modules.users.actor import Actor, ForbiddenError
from app.modules.users.models import PsychologistProfile, User, UserRole
from tests.conftest import (
    CSRF_HEADER,
    UNKNOWN_ID,
    AppointmentFactory,
    ClientFactory,
    ClientRecordFactory,
    FakeClock,
    SettingsFactory,
    UserFactory,
)
from tests.scheduling.conftest import (
    NOW,
    Booker,
    booking_body,
    error_code,
    load_appointment,
    local,
    logged_in,
)

pytestmark = pytest.mark.usefixtures("fixed_time", "working_hours")

MOVE = {"starts_at": local(12).isoformat()}


@dataclass(frozen=True)
class Scene:
    """Everything a route needs: `appointment` belongs to `booker` and to the psychologist."""

    profile: PsychologistProfile
    service: Service
    record: Client
    appointment: Appointment


Route = tuple[str, str, Callable[[Scene], dict[str, Any] | None]]


def no_body(_: Scene) -> None:
    return None


def book_body(scene: Scene) -> dict[str, Any]:
    return booking_body(scene.profile, scene.service, local(11))


def staff_body(scene: Scene) -> dict[str, Any]:
    return {
        "client_id": str(scene.record.id),
        "service_id": str(scene.service.id),
        "starts_at": local(11).isoformat(),
    }


CLIENT_ROUTES: list[Route] = [
    ("POST", "/appointments", book_body),
    ("GET", "/me/appointments", no_body),
    ("POST", "/appointments/{id}/cancel", no_body),
    ("POST", "/appointments/{id}/reschedule", lambda _: MOVE),
]
STAFF_READ_ROUTES: list[Route] = [
    ("GET", "/staff/appointments", no_body),
    ("GET", "/staff/appointments/{id}", no_body),
]
PSYCHOLOGIST_ROUTES: list[Route] = [
    ("POST", "/staff/appointments", staff_body),
    ("POST", "/staff/appointments/{id}/status", lambda _: {"status": "cancelled"}),
    ("POST", "/staff/appointments/{id}/reschedule", lambda _: MOVE),
]
ALL_ROUTES = CLIENT_ROUTES + STAFF_READ_ROUTES + PSYCHOLOGIST_ROUTES
WRITE_ROUTES = [route for route in ALL_ROUTES if route[0] != "GET"]


def route_id(route: Route) -> str:
    return f"{route[0]} {route[1]}"


client_routes = pytest.mark.parametrize("route", CLIENT_ROUTES, ids=route_id)
staff_read_routes = pytest.mark.parametrize("route", STAFF_READ_ROUTES, ids=route_id)
psychologist_routes = pytest.mark.parametrize("route", PSYCHOLOGIST_ROUTES, ids=route_id)
all_routes = pytest.mark.parametrize("route", ALL_ROUTES, ids=route_id)
write_routes = pytest.mark.parametrize("route", WRITE_ROUTES, ids=route_id)


@pytest.fixture
async def scene(
    therapist: User,
    profile: PsychologistProfile,
    service: Service,
    booker: Booker,
    make_appointment: AppointmentFactory,
    make_client_record: ClientRecordFactory,
) -> Scene:
    appointment = await make_appointment(
        therapist,
        booker.record,
        service_id=service.id,
        starts_at=local(9),
        ends_at=local(9, 50),
    )
    return Scene(profile, service, await make_client_record(therapist), appointment)


async def call(client: httpx.AsyncClient, route: Route, scene: Scene) -> httpx.Response:
    method, path, body = route
    return await client.request(method, path.format(id=scene.appointment.id), json=body(scene))


class TestClientRoutes:
    @client_routes
    async def test_a_client_may_call_them(self, booker: Booker, scene: Scene, route: Route) -> None:
        response = await call(booker.api, route, scene)

        assert response.status_code in {200, 201}

    @client_routes
    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.PSYCHOLOGIST])
    async def test_staff_get_403(
        self,
        new_client: ClientFactory,
        make_user: UserFactory,
        scene: Scene,
        route: Route,
        role: UserRole,
        db_session: AsyncSession,
    ) -> None:
        client = await logged_in(new_client, make_user, role)

        response = await call(client, route, scene)

        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"
        loaded = await load_appointment(db_session, scene.appointment.id)
        assert loaded.status is AppointmentStatus.CONFIRMED


class TestStaffReadRoutes:
    @staff_read_routes
    async def test_the_psychologist_may_read(
        self, therapist_api: httpx.AsyncClient, scene: Scene, route: Route
    ) -> None:
        response = await call(therapist_api, route, scene)

        assert response.status_code == 200

    @staff_read_routes
    async def test_an_admin_may_read(
        self, new_client: ClientFactory, make_user: UserFactory, scene: Scene, route: Route
    ) -> None:
        client = await logged_in(new_client, make_user, UserRole.ADMIN)

        response = await call(client, route, scene)

        assert response.status_code == 200

    @staff_read_routes
    async def test_a_client_gets_403(self, booker: Booker, scene: Scene, route: Route) -> None:
        response = await call(booker.api, route, scene)

        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"


class TestPsychologistRoutes:
    @psychologist_routes
    async def test_the_psychologist_may_call_them(
        self, therapist_api: httpx.AsyncClient, scene: Scene, route: Route
    ) -> None:
        response = await call(therapist_api, route, scene)

        assert response.status_code in {200, 201}

    @psychologist_routes
    async def test_an_admin_gets_403_and_changes_nothing(
        self,
        new_client: ClientFactory,
        make_user: UserFactory,
        scene: Scene,
        route: Route,
        db_session: AsyncSession,
    ) -> None:
        client = await logged_in(new_client, make_user, UserRole.ADMIN)

        response = await call(client, route, scene)

        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"
        loaded = await load_appointment(db_session, scene.appointment.id)
        assert loaded.status is AppointmentStatus.CONFIRMED

    @psychologist_routes
    async def test_a_client_gets_403(self, booker: Booker, scene: Scene, route: Route) -> None:
        response = await call(booker.api, route, scene)

        assert response.status_code == 403


class TestEveryRoute:
    @all_routes
    async def test_anonymous_callers_get_401(
        self, api: httpx.AsyncClient, scene: Scene, route: Route
    ) -> None:
        response = await call(api, route, scene)

        assert response.status_code == 401
        assert error_code(response) == "NOT_AUTHENTICATED"

    @write_routes
    async def test_writes_require_a_valid_csrf_token(
        self,
        booker: Booker,
        therapist_api: httpx.AsyncClient,
        scene: Scene,
        route: Route,
    ) -> None:
        client = booker.api if route in CLIENT_ROUTES else therapist_api
        method, path, body = route

        response = await client.request(
            method,
            path.format(id=scene.appointment.id),
            json=body(scene),
            headers={CSRF_HEADER: "forged"},
        )

        assert response.status_code == 403
        assert error_code(response) == "CSRF_INVALID"


class TestWhenTwoFactorIsMandatory:
    @pytest.fixture
    def api_settings(self) -> dict[str, Any]:
        return {"require_staff_2fa": True}

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.PSYCHOLOGIST])
    async def test_staff_without_two_factor_are_blocked(
        self, new_client: ClientFactory, make_user: UserFactory, role: UserRole
    ) -> None:
        client = await logged_in(new_client, make_user, role)

        response = await client.get("/staff/appointments")

        assert response.status_code == 403
        assert error_code(response) == "TWO_FACTOR_SETUP_REQUIRED"


class TestOtherPeoplesAppointments:
    async def test_a_psychologist_cannot_see_who_a_colleague_treats(
        self,
        scene: Scene,
        new_client: ClientFactory,
        make_user: UserFactory,
    ) -> None:
        client = await logged_in(new_client, make_user, UserRole.PSYCHOLOGIST)

        response = await client.get("/staff/appointments")

        assert response.json()["items"] == []
        assert response.json()["total"] == 0
        detail = await client.get(f"/staff/appointments/{scene.appointment.id}")
        assert detail.status_code == 404


class TestSchedulingServiceWithoutARouter:
    """The future agent calls services directly, so each one must authorize by itself."""

    @pytest.fixture
    def service_under_test(
        self, db_session: AsyncSession, make_settings: SettingsFactory, clock: FakeClock
    ) -> SchedulingService:
        return build_scheduling_service(db_session, make_settings(), clock)

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.PSYCHOLOGIST])
    @pytest.mark.parametrize(
        "call_service",
        [
            lambda service, actor: service.book_appointment(
                actor,
                BookAppointmentRequest(psychologist_slug="a", service_id=UNKNOWN_ID, starts_at=NOW),
                ip_address=None,
            ),
            lambda service, actor: service.list_own_appointments(
                actor, AppointmentWhen.ALL, PageParams()
            ),
            lambda service, actor: service.cancel_own_appointment(
                actor, UNKNOWN_ID, ip_address=None
            ),
            lambda service, actor: service.reschedule_own_appointment(
                actor, UNKNOWN_ID, RescheduleAppointmentRequest(starts_at=NOW), ip_address=None
            ),
        ],
        ids=["book", "list_own", "cancel_own", "reschedule_own"],
    )
    async def test_only_clients_may_use_the_client_methods(
        self,
        service_under_test: SchedulingService,
        call_service: Callable[[SchedulingService, Actor], Awaitable[Any]],
        role: UserRole,
    ) -> None:
        with pytest.raises(ForbiddenError):
            await call_service(service_under_test, Actor(user_id=uuid.uuid4(), role=role))

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
    @pytest.mark.parametrize(
        "call_service",
        [
            lambda service, actor: service.create_appointment(
                actor,
                CreateStaffAppointmentRequest(
                    client_id=UNKNOWN_ID, service_id=UNKNOWN_ID, starts_at=NOW
                ),
                ip_address=None,
            ),
            lambda service, actor: service.change_status(
                actor,
                UNKNOWN_ID,
                ChangeAppointmentStatusRequest(status=AppointmentStatus.CONFIRMED),
                ip_address=None,
            ),
            lambda service, actor: service.reschedule_appointment(
                actor, UNKNOWN_ID, RescheduleAppointmentRequest(starts_at=NOW), ip_address=None
            ),
        ],
        ids=["create", "change_status", "reschedule"],
    )
    async def test_only_psychologists_may_change_appointments(
        self,
        service_under_test: SchedulingService,
        call_service: Callable[[SchedulingService, Actor], Awaitable[Any]],
        role: UserRole,
    ) -> None:
        with pytest.raises(ForbiddenError):
            await call_service(service_under_test, Actor(user_id=uuid.uuid4(), role=role))

    @pytest.mark.parametrize(
        "read",
        [
            lambda service, actor: service.list_appointments(actor, PageParams()),
            lambda service, actor: service.get_appointment(actor, UNKNOWN_ID),
        ],
        ids=["list", "detail"],
    )
    async def test_clients_may_not_read_the_staff_view(
        self,
        service_under_test: SchedulingService,
        read: Callable[[SchedulingService, Actor], Awaitable[Any]],
    ) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=UserRole.CLIENT)

        with pytest.raises(ForbiddenError):
            await read(service_under_test, actor)


async def test_routes_declare_the_roles_that_may_call_them(api_app: FastAPI) -> None:
    declared = {
        (method, path): route_policies(route.dependant)
        for path, route in api_routes(api_app)
        for method in route.methods or ()
        if "appointments" in path
    }

    assert set(declared) == {
        (method, "/api/v1" + path.format(id="{appointment_id}")) for method, path, _ in ALL_ROUTES
    }
    for route_list, expected in (
        (CLIENT_ROUTES, "roles:client"),
        (STAFF_READ_ROUTES, "roles:admin,psychologist"),
        (PSYCHOLOGIST_ROUTES, "roles:psychologist"),
    ):
        for method, path, _ in route_list:
            full_path = "/api/v1" + path.format(id="{appointment_id}")
            assert expected in declared[method, full_path]
