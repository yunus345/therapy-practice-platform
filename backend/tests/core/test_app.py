import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.core.config import Environment
from app.core.errors import AppError
from app.main import create_app
from tests.conftest import SettingsFactory


class SlotTakenError(AppError):
    status_code = 409
    code = "SLOT_TAKEN"
    message = "The slot is taken."


class Payload(BaseModel):
    email: str
    age: int


def add_error_routes(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/conflict")
    async def conflict() -> None:
        raise SlotTakenError(details={"slot": "x"})

    @app.post("/echo")
    async def echo(payload: Payload) -> Payload:
        return payload


async def test_health_reports_ok_when_database_is_reachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_returns_503_without_details_when_database_is_down(
    make_settings: SettingsFactory,
) -> None:
    app = create_app(
        make_settings(
            database_url="postgresql+asyncpg://nobody:hidden-password@127.0.0.1:1/missing"
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "hidden-password" not in response.text
    await app.state.database.dispose()


async def test_app_error_uses_the_error_shape(app: FastAPI, client: httpx.AsyncClient) -> None:
    add_error_routes(app)

    response = await client.get("/conflict")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "SLOT_TAKEN", "message": "The slot is taken.", "details": {"slot": "x"}}
    }


async def test_unhandled_error_returns_generic_500_with_headers(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    add_error_routes(app)

    response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "details": {},
        }
    }
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_unknown_route_uses_the_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_wrong_method_uses_the_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["Allow"]


async def test_validation_error_does_not_echo_submitted_values(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    add_error_routes(app)

    response = await client.post("/echo", json={"email": "secret@example.com", "age": "not-a-num"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"] == [{"field": "body.age", "code": "int_parsing"}]
    assert "secret@example.com" not in response.text
    assert "not-a-num" not in response.text


async def test_malformed_json_uses_the_error_shape(app: FastAPI, client: httpx.AsyncClient) -> None:
    add_error_routes(app)

    response = await client.post(
        "/echo", content="{broken", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_request_id_is_generated_and_inbound_value_is_ignored(
    client: httpx.AsyncClient,
) -> None:
    first = await client.get("/api/v1/health", headers={"X-Request-ID": "injected\tvalue"})
    second = await client.get("/api/v1/health")

    assert first.headers["X-Request-ID"] != "injected\tvalue"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_security_headers_are_set(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert "Strict-Transport-Security" not in response.headers


async def test_docs_page_allows_swagger_assets(client: httpx.AsyncClient) -> None:
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "cdn.jsdelivr.net" in response.headers["Content-Security-Policy"]


async def test_production_sets_hsts_and_has_no_docs(make_settings: SettingsFactory) -> None:
    app = create_app(
        make_settings(
            environment=Environment.PRODUCTION,
            database_url="postgresql+asyncpg://app:strong@localhost/seans",
            migration_database_url="postgresql+asyncpg://owner:strong@localhost/seans",
            require_staff_2fa=True,
            frontend_origin="https://app.example.com",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")

    assert response.status_code == 404
    assert "max-age" in response.headers["Strict-Transport-Security"]
    await app.state.database.dispose()


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [("http://localhost:3000", True), ("https://evil.example.com", False)],
)
async def test_cors_allows_only_the_configured_origin(
    client: httpx.AsyncClient, origin: str, allowed: bool
) -> None:
    response = await client.options(
        "/api/v1/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )

    assert ("Access-Control-Allow-Origin" in response.headers) is allowed
    if allowed:
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
