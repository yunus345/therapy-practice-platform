from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import MAX_OFFSET
from app.modules.audit.service import AuditAction
from app.modules.catalog.models import DeliveryMode, Service
from app.modules.users.models import User
from tests.conftest import UNKNOWN_ID, ServiceFactory, audit_entries

SERVICE = {
    "name": "Individual Therapy",
    "description": "A session for adults",
    "duration_minutes": 50,
    "price_amount": "1500.00",
    "currency": "EUR",
    "delivery_mode": "online",
}


async def test_admin_creates_a_service(
    api: httpx.AsyncClient, admin: User, db_session: AsyncSession
) -> None:
    response = await api.post("/admin/services", json=SERVICE)

    assert response.status_code == 201
    body = response.json()
    assert {name: body[name] for name in SERVICE} == SERVICE
    assert body["is_active"] is True
    assert body["created_at"] == body["updated_at"]

    service = await db_session.get(Service, body["id"])
    assert service is not None
    assert service.price_amount == Decimal("1500.00")
    assert service.delivery_mode is DeliveryMode.ONLINE


async def test_description_and_currency_are_optional(api: httpx.AsyncClient, admin: User) -> None:
    body = {key: value for key, value in SERVICE.items() if key not in {"description", "currency"}}

    response = await api.post("/admin/services", json=body)

    assert response.status_code == 201
    assert response.json()["description"] is None
    assert response.json()["currency"] == "TRY"


@pytest.mark.parametrize("price", [1500, "1500", 1500.0, "1500.00"])
async def test_price_always_reads_with_two_decimals(
    api: httpx.AsyncClient, admin: User, price: int | float | str
) -> None:
    created = await api.post("/admin/services", json=SERVICE | {"price_amount": price})

    fetched = await api.get(f"/admin/services/{created.json()['id']}")

    assert created.json()["price_amount"] == "1500.00"
    assert fetched.json()["price_amount"] == "1500.00"


@pytest.mark.parametrize("duration", [1, 480])
async def test_duration_accepts_the_limits_of_the_range(
    api: httpx.AsyncClient, admin: User, duration: int
) -> None:
    response = await api.post("/admin/services", json=SERVICE | {"duration_minutes": duration})

    assert response.status_code == 201


INVALID_FIELDS: list[dict[str, Any]] = [
    {"name": ""},
    {"name": "   "},
    {"name": "a" * 201},
    {"name": "a\x00b"},
    {"description": "a" * 2001},
    {"duration_minutes": 0},
    {"duration_minutes": 481},
    {"price_amount": -1},
    {"price_amount": "10.001"},
    {"price_amount": "100000000"},
    {"price_amount": "NaN"},
    {"currency": "try"},
    {"currency": "TRYY"},
    {"delivery_mode": "telepathy"},
]


@pytest.mark.parametrize("change", INVALID_FIELDS)
async def test_create_rejects_invalid_values(
    api: httpx.AsyncClient, admin: User, change: dict[str, Any]
) -> None:
    response = await api.post("/admin/services", json=SERVICE | change)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("missing", ["name", "duration_minutes", "price_amount", "delivery_mode"])
async def test_create_requires_the_essential_fields(
    api: httpx.AsyncClient, admin: User, missing: str
) -> None:
    body = {key: value for key, value in SERVICE.items() if key != missing}

    response = await api.post("/admin/services", json=body)

    assert response.status_code == 422


async def test_list_shows_active_and_inactive_services_by_name(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    await make_service(name="Couples Therapy")
    await make_service(name="Art Therapy", is_active=False)

    response = await api.get("/admin/services")

    assert response.status_code == 200
    body = response.json()
    assert [(item["name"], item["is_active"]) for item in body["items"]] == [
        ("Art Therapy", False),
        ("Couples Therapy", True),
    ]
    assert (body["total"], body["limit"], body["offset"]) == (2, 50, 0)


async def test_list_is_paginated_with_a_stable_order(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    services = [await make_service(name="Same Name") for _ in range(3)]

    pages = [
        await api.get("/admin/services", params={"limit": 2, "offset": offset}) for offset in (0, 2)
    ]

    assert [len(page.json()["items"]) for page in pages] == [2, 1]
    assert {page.json()["total"] for page in pages} == {3}
    returned = [item["id"] for page in pages for item in page.json()["items"]]
    assert sorted(returned) == sorted(str(service.id) for service in services)


async def test_largest_offset_reaches_the_database_without_an_error(
    api: httpx.AsyncClient, admin: User
) -> None:
    response = await api.get("/admin/services", params={"offset": MAX_OFFSET})

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_admin_reads_one_service(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    service = await make_service(name="Child Therapy")

    response = await api.get(f"/admin/services/{service.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Child Therapy"


async def test_patch_changes_only_the_given_fields(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    service = await make_service(description="Short text", duration_minutes=45)

    response = await api.patch(
        f"/admin/services/{service.id}",
        json={"name": "New Name", "price_amount": 2000, "currency": "USD"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["price_amount"], body["currency"]) == (
        "New Name",
        "2000.00",
        "USD",
    )
    assert (body["description"], body["duration_minutes"]) == ("Short text", 45)


async def test_patch_can_clear_the_description(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    service = await make_service(description="To be removed")

    response = await api.patch(f"/admin/services/{service.id}", json={"description": None})

    assert response.status_code == 200
    assert response.json()["description"] is None


@pytest.mark.parametrize(
    "field",
    ["name", "duration_minutes", "price_amount", "currency", "delivery_mode", "is_active"],
)
async def test_patch_rejects_null_for_required_columns(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory, field: str
) -> None:
    service = await make_service()

    response = await api.patch(f"/admin/services/{service.id}", json={field: None})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"] == [{"field": f"body.{field}", "code": "null_not_allowed"}]


@pytest.mark.parametrize("change", INVALID_FIELDS)
async def test_patch_rejects_invalid_values(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory, change: dict[str, Any]
) -> None:
    service = await make_service()

    response = await api.patch(f"/admin/services/{service.id}", json=change)

    assert response.status_code == 422


async def test_delete_deactivates_the_service_and_keeps_the_row(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    service = await make_service()

    response = await api.delete(f"/admin/services/{service.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert (await api.get(f"/admin/services/{service.id}")).json()["is_active"] is False


async def test_deleting_twice_is_harmless_and_audited_once(
    api: httpx.AsyncClient,
    admin: User,
    make_service: ServiceFactory,
    db_session: AsyncSession,
) -> None:
    service = await make_service()

    responses = [await api.delete(f"/admin/services/{service.id}") for _ in range(2)]

    assert {response.status_code for response in responses} == {204}
    assert len(await audit_entries(db_session, AuditAction.SERVICE_DEACTIVATED)) == 1


async def test_patch_can_reactivate_a_service(
    api: httpx.AsyncClient, admin: User, make_service: ServiceFactory
) -> None:
    service = await make_service(is_active=False)

    response = await api.patch(f"/admin/services/{service.id}", json={"is_active": True})

    assert response.status_code == 200
    assert response.json()["is_active"] is True


@pytest.mark.parametrize(
    ("method", "body"), [("GET", None), ("PATCH", {"name": "New"}), ("DELETE", None)]
)
async def test_unknown_service_returns_404(
    api: httpx.AsyncClient, admin: User, method: str, body: dict[str, str] | None
) -> None:
    response = await api.request(method, f"/admin/services/{UNKNOWN_ID}", json=body)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_writes_are_audited_with_ids_and_field_names_only(
    api: httpx.AsyncClient, admin: User, db_session: AsyncSession
) -> None:
    created = await api.post("/admin/services", json=SERVICE)
    service_id = created.json()["id"]
    await api.patch(
        f"/admin/services/{service_id}", json={"name": "Secret Name", "description": None}
    )
    await api.delete(f"/admin/services/{service_id}")

    entries = [
        entry
        for action in (
            AuditAction.SERVICE_CREATED,
            AuditAction.SERVICE_UPDATED,
            AuditAction.SERVICE_DEACTIVATED,
        )
        for entry in await audit_entries(db_session, action)
    ]

    assert len(entries) == 3
    assert {str(entry.entity_id) for entry in entries} == {service_id}
    assert {entry.entity_type for entry in entries} == {"service"}
    assert {entry.actor_user_id for entry in entries} == {admin.id}
    assert {str(entry.ip_address) for entry in entries} == {"127.0.0.1"}
    assert [entry.event_metadata for entry in entries] == [
        {},
        {"changed_fields": ["description", "name"]},
        {},
    ]


async def test_patch_that_changes_nothing_is_not_audited(
    api: httpx.AsyncClient,
    admin: User,
    make_service: ServiceFactory,
    db_session: AsyncSession,
) -> None:
    service = await make_service(name="Same Name", price_amount=Decimal("1500.00"))

    responses = [
        await api.patch(f"/admin/services/{service.id}", json=body)
        for body in ({}, {"name": "Same Name", "price_amount": 1500})
    ]

    assert {response.status_code for response in responses} == {200}
    assert await audit_entries(db_session, AuditAction.SERVICE_UPDATED) == []


async def test_failed_patch_writes_neither_change_nor_audit_entry(
    api: httpx.AsyncClient,
    admin: User,
    make_service: ServiceFactory,
    db_session: AsyncSession,
) -> None:
    service = await make_service(name="Old Name")

    response = await api.patch(
        f"/admin/services/{service.id}", json={"name": "New Name", "currency": None}
    )

    assert response.status_code == 422
    await db_session.refresh(service)
    assert service.name == "Old Name"
    assert await audit_entries(db_session, AuditAction.SERVICE_UPDATED) == []
