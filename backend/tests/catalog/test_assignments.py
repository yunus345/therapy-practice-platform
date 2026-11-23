import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditAction
from app.modules.catalog.models import PsychologistService, Service
from app.modules.users.models import User, UserRole
from tests.conftest import (
    UNKNOWN_ID,
    ClientFactory,
    ServiceAssigner,
    ServiceFactory,
    UserFactory,
    audit_entries,
    log_in,
)


@pytest.fixture
async def service(make_service: ServiceFactory) -> Service:
    return await make_service(name="Individual Therapy", price_amount=Decimal("1500.00"))


def assignment_path(psychologist_id: uuid.UUID, service_id: uuid.UUID) -> str:
    return f"/admin/psychologists/{psychologist_id}/services/{service_id}"


async def assignment_rows(session: AsyncSession, psychologist: User) -> list[PsychologistService]:
    result = await session.scalars(
        select(PsychologistService)
        .where(PsychologistService.psychologist_id == psychologist.id)
        .execution_options(populate_existing=True)
    )
    return list(result.all())


async def test_put_assigns_a_service_with_a_custom_price(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    service: Service,
    db_session: AsyncSession,
) -> None:
    response = await api.put(
        assignment_path(therapist.id, service.id), json={"custom_price_amount": "1200.50"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(service.id)
    assert body["custom_price_amount"] == "1200.50"
    assert body["price_amount"] == "1200.50"
    assert body["is_active"] is True
    rows = await assignment_rows(db_session, therapist)
    assert [row.custom_price_amount for row in rows] == [Decimal("1200.50")]


async def test_put_without_a_price_uses_the_list_price(
    api: httpx.AsyncClient, admin: User, therapist: User, service: Service
) -> None:
    for body in ({}, {"custom_price_amount": None}):
        response = await api.put(assignment_path(therapist.id, service.id), json=body)

        assert response.status_code == 200
        assert response.json()["custom_price_amount"] is None
        assert response.json()["price_amount"] == "1500.00"


async def test_put_is_an_idempotent_upsert_on_one_row(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    service: Service,
    db_session: AsyncSession,
) -> None:
    path = assignment_path(therapist.id, service.id)

    await api.put(path, json={"custom_price_amount": 1000})
    first = await assignment_rows(db_session, therapist)
    await api.put(path, json={"custom_price_amount": 1000})
    await api.put(path, json={"custom_price_amount": 900})
    response = await api.put(path, json={"custom_price_amount": None})

    assert response.status_code == 200
    rows = await assignment_rows(db_session, therapist)
    assert [row.id for row in rows] == [first[0].id]
    assert rows[0].custom_price_amount is None


async def test_a_custom_price_of_zero_is_a_price(
    api: httpx.AsyncClient, admin: User, therapist: User, service: Service
) -> None:
    response = await api.put(
        assignment_path(therapist.id, service.id), json={"custom_price_amount": 0}
    )

    assert response.json()["price_amount"] == "0.00"
    assert response.json()["custom_price_amount"] == "0.00"


@pytest.mark.parametrize("price", [-1, "10.001", "100000000", "free"])
async def test_put_rejects_invalid_prices(
    api: httpx.AsyncClient, admin: User, therapist: User, service: Service, price: int | str
) -> None:
    response = await api.put(
        assignment_path(therapist.id, service.id), json={"custom_price_amount": price}
    )

    assert response.status_code == 422


async def test_put_returns_404_for_an_unknown_psychologist_or_service(
    api: httpx.AsyncClient, admin: User, therapist: User, service: Service
) -> None:
    responses = [
        await api.put(assignment_path(UNKNOWN_ID, service.id), json={}),
        await api.put(assignment_path(therapist.id, UNKNOWN_ID), json={}),
    ]

    assert {response.status_code for response in responses} == {404}
    assert {response.json()["error"]["code"] for response in responses} == {"NOT_FOUND"}


@pytest.mark.parametrize("role", [UserRole.CLIENT, UserRole.ADMIN])
async def test_a_user_without_a_profile_is_not_a_psychologist(
    api: httpx.AsyncClient,
    admin: User,
    make_user: UserFactory,
    service: Service,
    role: UserRole,
) -> None:
    user = await make_user(role)

    assigned = await api.put(assignment_path(user.id, service.id), json={})
    listed = await api.get(f"/admin/psychologists/{user.id}/services")

    assert (assigned.status_code, listed.status_code) == (404, 404)


async def test_delete_removes_only_that_assignment(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    make_user: UserFactory,
    service: Service,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
    db_session: AsyncSession,
) -> None:
    colleague = await make_user(UserRole.PSYCHOLOGIST)
    other_service = await make_service()
    await assign_service(therapist, service)
    await assign_service(therapist, other_service)
    await assign_service(colleague, service)

    response = await api.delete(assignment_path(therapist.id, service.id))

    assert response.status_code == 204
    assert response.content == b""
    remaining = await assignment_rows(db_session, therapist)
    assert [row.service_id for row in remaining] == [other_service.id]
    assert len(await assignment_rows(db_session, colleague)) == 1


async def test_delete_returns_404_when_nothing_is_assigned(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    service: Service,
    assign_service: ServiceAssigner,
    db_session: AsyncSession,
) -> None:
    await assign_service(therapist, service)

    first = await api.delete(assignment_path(therapist.id, service.id))
    second = await api.delete(assignment_path(therapist.id, service.id))

    assert (first.status_code, second.status_code) == (204, 404)
    assert second.json()["error"]["code"] == "NOT_FOUND"
    assert len(await audit_entries(db_session, AuditAction.SERVICE_UNASSIGNED)) == 1


async def test_delete_returns_404_for_an_unknown_psychologist_or_service(
    api: httpx.AsyncClient, admin: User, therapist: User, service: Service
) -> None:
    responses = [
        await api.delete(assignment_path(UNKNOWN_ID, service.id)),
        await api.delete(assignment_path(therapist.id, UNKNOWN_ID)),
    ]

    assert {response.status_code for response in responses} == {404}


async def test_admin_lists_assignments_with_list_and_custom_prices(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    make_user: UserFactory,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    custom = await make_service(name="A Custom Price")
    standard = await make_service(name="B List Price", price_amount=Decimal("900.00"))
    inactive = await make_service(name="C Inactive", is_active=False)
    await assign_service(therapist, custom, Decimal("700.00"))
    await assign_service(therapist, standard)
    await assign_service(therapist, inactive)
    await assign_service(await make_user(UserRole.PSYCHOLOGIST), await make_service(name="D"))

    response = await api.get(f"/admin/psychologists/{therapist.id}/services")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [
        (item["name"], item["price_amount"], item["custom_price_amount"], item["is_active"])
        for item in body["items"]
    ] == [
        ("A Custom Price", "700.00", "700.00", True),
        ("B List Price", "900.00", None, True),
        ("C Inactive", "1500.00", None, False),
    ]


async def test_assignments_are_paginated(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    for name in ("A", "B", "C"):
        await assign_service(therapist, await make_service(name=name))

    response = await api.get(
        f"/admin/psychologists/{therapist.id}/services", params={"limit": 1, "offset": 2}
    )

    body = response.json()
    assert [item["name"] for item in body["items"]] == ["C"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 1, 2)


async def test_listing_an_unknown_psychologist_returns_404(
    api: httpx.AsyncClient, admin: User
) -> None:
    response = await api.get(f"/admin/psychologists/{UNKNOWN_ID}/services")

    assert response.status_code == 404


async def test_psychologist_sees_only_their_active_services_at_the_effective_price(
    api: httpx.AsyncClient,
    psychologist: User,
    make_user: UserFactory,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    custom = await make_service(
        name="A Custom", description="Details", price_amount=Decimal("1500.00")
    )
    standard = await make_service(name="B List", price_amount=Decimal("900.00"))
    await assign_service(psychologist, custom, Decimal("0.00"))
    await assign_service(psychologist, standard)
    await assign_service(psychologist, await make_service(name="C Inactive", is_active=False))
    await assign_service(await make_user(UserRole.PSYCHOLOGIST), await make_service(name="D Other"))
    await make_service(name="E Unassigned")

    response = await api.get("/staff/services")

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 50, 0)
    assert body["items"] == [
        {
            "id": str(custom.id),
            "name": "A Custom",
            "description": "Details",
            "duration_minutes": 50,
            "price_amount": "0.00",
            "currency": "TRY",
            "delivery_mode": "in_person",
        },
        {
            "id": str(standard.id),
            "name": "B List",
            "description": None,
            "duration_minutes": 50,
            "price_amount": "900.00",
            "currency": "TRY",
            "delivery_mode": "in_person",
        },
    ]


async def test_own_services_are_paginated(
    api: httpx.AsyncClient,
    psychologist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    for name in ("A", "B", "C"):
        await assign_service(psychologist, await make_service(name=name))

    response = await api.get("/staff/services", params={"limit": 2, "offset": 1})

    body = response.json()
    assert [item["name"] for item in body["items"]] == ["B", "C"]
    assert body["total"] == 3


async def test_a_new_assignment_reaches_the_psychologist(
    api: httpx.AsyncClient,
    admin: User,
    new_client: ClientFactory,
    therapist: User,
    service: Service,
) -> None:
    staff = await new_client()
    await log_in(staff, therapist.email)

    await api.put(assignment_path(therapist.id, service.id), json={"custom_price_amount": 1100})
    assigned = await staff.get("/staff/services")
    await api.delete(assignment_path(therapist.id, service.id))
    unassigned = await staff.get("/staff/services")

    assert [item["price_amount"] for item in assigned.json()["items"]] == ["1100.00"]
    assert unassigned.json()["items"] == []


async def test_assignment_writes_are_audited_without_prices(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    service: Service,
    db_session: AsyncSession,
) -> None:
    path = assignment_path(therapist.id, service.id)

    await api.put(path, json={"custom_price_amount": 1100})
    await api.delete(path)

    for action in (AuditAction.SERVICE_ASSIGNED, AuditAction.SERVICE_UNASSIGNED):
        [entry] = await audit_entries(db_session, action)
        assert entry.entity_type == "service"
        assert entry.entity_id == service.id
        assert entry.actor_user_id == admin.id
        assert entry.event_metadata == {"psychologist_id": str(therapist.id)}


async def test_rejected_assignment_is_neither_stored_nor_audited(
    api: httpx.AsyncClient,
    admin: User,
    therapist: User,
    db_session: AsyncSession,
) -> None:
    response = await api.put(assignment_path(therapist.id, UNKNOWN_ID), json={})

    assert response.status_code == 404
    count = await db_session.scalar(select(func.count()).select_from(PsychologistService))
    assert count == 0
    assert await audit_entries(db_session, AuditAction.SERVICE_ASSIGNED) == []
