from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.dependencies import api_routes, route_policies
from app.modules.clients.models import Client
from app.modules.users.models import User, UserRole
from tests.clients.conftest import load_client, load_relationship
from tests.conftest import (
    CSRF_HEADER,
    ClientFactory,
    ClientRecordFactory,
    UserFactory,
    log_in,
)

BODIES: dict[str, dict[str, Any]] = {
    "POST": {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "phone": "+90 532 123 45 67",
        "kvkk_disclosure_given": True,
    },
    "PATCH": {"first_name": "Changed"},
}
READ_ROUTES = [("GET", "/staff/clients"), ("GET", "/staff/clients/{client}")]
WRITE_ROUTES = [
    ("POST", "/staff/clients"),
    ("PATCH", "/staff/clients/{client}"),
    ("DELETE", "/staff/clients/{client}"),
]
ALL_ROUTES = READ_ROUTES + WRITE_ROUTES

all_routes = pytest.mark.parametrize(("method", "path"), ALL_ROUTES)
write_routes = pytest.mark.parametrize(("method", "path"), WRITE_ROUTES)


async def logged_in(
    new_client: ClientFactory, make_user: UserFactory, role: UserRole
) -> httpx.AsyncClient:
    client = await new_client()
    await log_in(client, (await make_user(role)).email)
    return client


@pytest.fixture
async def record(colleague: User, make_client_record: ClientRecordFactory) -> Client:
    return await make_client_record(colleague, first_name="Ada")


@all_routes
async def test_psychologist_can_call_every_route_on_their_client(
    colleague_api: httpx.AsyncClient, record: Client, method: str, path: str
) -> None:
    response = await colleague_api.request(
        method, path.format(client=record.id), json=BODIES.get(method)
    )

    assert response.status_code in {200, 201, 204}


@pytest.mark.parametrize(("method", "path"), READ_ROUTES)
async def test_admin_can_read(
    new_client: ClientFactory, make_user: UserFactory, record: Client, method: str, path: str
) -> None:
    client = await logged_in(new_client, make_user, UserRole.ADMIN)

    response = await client.request(method, path.format(client=record.id))

    assert response.status_code == 200


@write_routes
async def test_admin_gets_403_on_writes_and_changes_nothing(
    new_client: ClientFactory,
    make_user: UserFactory,
    colleague: User,
    record: Client,
    db_session: AsyncSession,
    method: str,
    path: str,
) -> None:
    client = await logged_in(new_client, make_user, UserRole.ADMIN)

    response = await client.request(method, path.format(client=record.id), json=BODIES.get(method))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert (await load_client(db_session, record)).first_name == "Ada"
    assert (await load_relationship(db_session, colleague, record)).ended_at is None


@all_routes
async def test_clients_get_403(
    new_client: ClientFactory, make_user: UserFactory, record: Client, method: str, path: str
) -> None:
    client = await logged_in(new_client, make_user, UserRole.CLIENT)

    response = await client.request(method, path.format(client=record.id), json=BODIES.get(method))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@all_routes
async def test_anonymous_callers_get_401(
    api: httpx.AsyncClient, record: Client, method: str, path: str
) -> None:
    response = await api.request(method, path.format(client=record.id), json=BODIES.get(method))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@write_routes
async def test_writes_require_a_valid_csrf_token(
    colleague_api: httpx.AsyncClient, record: Client, method: str, path: str
) -> None:
    response = await colleague_api.request(
        method,
        path.format(client=record.id),
        json=BODIES.get(method),
        headers={CSRF_HEADER: "forged"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


class TestWhenTwoFactorIsMandatory:
    @pytest.fixture
    def api_settings(self) -> dict[str, Any]:
        return {"require_staff_2fa": True}

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.PSYCHOLOGIST])
    async def test_staff_without_two_factor_are_blocked(
        self, new_client: ClientFactory, make_user: UserFactory, role: UserRole
    ) -> None:
        client = await logged_in(new_client, make_user, role)

        response = await client.get("/staff/clients")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "TWO_FACTOR_SETUP_REQUIRED"


async def test_routes_declare_the_roles_that_may_call_them(api_app: FastAPI) -> None:
    declared = {
        (method, path): route_policies(route.dependant)
        for path, route in api_routes(api_app)
        for method in route.methods or ()
        if path.startswith("/api/v1/staff/clients")
    }

    assert set(declared) == {
        (method, f"/api/v1{path.format(client='{client_id}')}") for method, path in ALL_ROUTES
    }
    for (method, _), policies in declared.items():
        expected = "roles:admin,psychologist" if method == "GET" else "roles:psychologist"
        assert expected in policies


async def test_routes_appear_in_the_openapi_document(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert set(paths["/api/v1/staff/clients"]) == {"get", "post"}
    assert set(paths["/api/v1/staff/clients/{client_id}"]) == {"get", "patch", "delete"}
