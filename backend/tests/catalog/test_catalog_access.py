from typing import Any

import httpx
import pytest

from app.modules.catalog.models import Service
from app.modules.users.models import User, UserRole
from tests.conftest import (
    CSRF_HEADER,
    ClientFactory,
    ServiceAssigner,
    ServiceFactory,
    UserFactory,
    log_in,
)

SERVICE = {
    "name": "Individual Therapy",
    "duration_minutes": 50,
    "price_amount": "1500.00",
    "delivery_mode": "online",
}

ADMIN_ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/admin/services", None),
    ("POST", "/admin/services", SERVICE),
    ("GET", "/admin/services/{service}", None),
    ("PATCH", "/admin/services/{service}", {"name": "New Name"}),
    ("DELETE", "/admin/services/{service}", None),
    ("GET", "/admin/psychologists/{psychologist}/services", None),
    (
        "PUT",
        "/admin/psychologists/{psychologist}/services/{service}",
        {"custom_price_amount": None},
    ),
    ("DELETE", "/admin/psychologists/{psychologist}/services/{service}", None),
]

admin_routes = pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)


@pytest.fixture
async def service(
    make_service: ServiceFactory, assign_service: ServiceAssigner, therapist: User
) -> Service:
    service = await make_service()
    await assign_service(therapist, service)
    return service


async def logged_in(
    new_client: ClientFactory, make_user: UserFactory, role: UserRole
) -> httpx.AsyncClient:
    client = await new_client()
    await log_in(client, (await make_user(role)).email)
    return client


@admin_routes
async def test_admin_can_call_catalog_routes(
    new_client: ClientFactory,
    make_user: UserFactory,
    therapist: User,
    service: Service,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    client = await logged_in(new_client, make_user, UserRole.ADMIN)
    url = path.format(service=service.id, psychologist=therapist.id)

    response = await client.request(method, url, json=body)

    assert response.status_code in {200, 201, 204}


@admin_routes
@pytest.mark.parametrize("role", [UserRole.PSYCHOLOGIST, UserRole.CLIENT])
async def test_other_roles_get_403_on_admin_catalog_routes(
    new_client: ClientFactory,
    make_user: UserFactory,
    therapist: User,
    service: Service,
    role: UserRole,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    client = await logged_in(new_client, make_user, role)
    url = path.format(service=service.id, psychologist=therapist.id)

    response = await client.request(method, url, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@admin_routes
async def test_anonymous_callers_get_401_on_admin_catalog_routes(
    api: httpx.AsyncClient,
    therapist: User,
    service: Service,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    url = path.format(service=service.id, psychologist=therapist.id)

    response = await api.request(method, url, json=body)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_psychologist_can_list_own_services(
    new_client: ClientFactory, make_user: UserFactory
) -> None:
    client = await logged_in(new_client, make_user, UserRole.PSYCHOLOGIST)

    response = await client.get("/staff/services")

    assert response.status_code == 200


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
async def test_other_roles_get_403_on_own_services(
    new_client: ClientFactory, make_user: UserFactory, role: UserRole
) -> None:
    client = await logged_in(new_client, make_user, role)

    response = await client.get("/staff/services")

    assert response.status_code == 403


async def test_anonymous_callers_get_401_on_own_services(api: httpx.AsyncClient) -> None:
    response = await api.get("/staff/services")

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
async def test_catalog_writes_require_a_valid_csrf_token(
    api: httpx.AsyncClient, admin: User, therapist: User, service: Service, method: str
) -> None:
    paths = {
        "POST": "/admin/services",
        "PATCH": f"/admin/services/{service.id}",
        "PUT": f"/admin/psychologists/{therapist.id}/services/{service.id}",
        "DELETE": f"/admin/services/{service.id}",
    }

    response = await api.request(
        method, paths[method], json=SERVICE, headers={CSRF_HEADER: "forged"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


class TestWhenTwoFactorIsMandatory:
    @pytest.fixture
    def api_settings(self) -> dict[str, Any]:
        return {"require_staff_2fa": True}

    @pytest.mark.parametrize(
        ("role", "path"),
        [(UserRole.ADMIN, "/admin/services"), (UserRole.PSYCHOLOGIST, "/staff/services")],
    )
    async def test_staff_without_two_factor_are_blocked(
        self, new_client: ClientFactory, make_user: UserFactory, role: UserRole, path: str
    ) -> None:
        client = await logged_in(new_client, make_user, role)

        response = await client.get(path)

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "TWO_FACTOR_SETUP_REQUIRED"


async def test_new_routes_appear_in_the_openapi_document(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert set(paths["/api/v1/admin/services"]) == {"get", "post"}
    assert set(paths["/api/v1/admin/services/{service_id}"]) == {"get", "patch", "delete"}
    assert set(paths["/api/v1/admin/psychologists/{psychologist_id}/services"]) == {"get"}
    assert set(paths["/api/v1/admin/psychologists/{psychologist_id}/services/{service_id}"]) == {
        "put",
        "delete",
    }
    assert set(paths["/api/v1/staff/services"]) == {"get"}
    assert set(paths["/api/v1/staff/profile"]) == {"get", "patch"}
