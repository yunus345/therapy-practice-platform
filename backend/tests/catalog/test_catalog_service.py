import dataclasses
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.modules.catalog.models import DeliveryMode
from app.modules.catalog.schemas import (
    AssignServiceRequest,
    CreateServiceRequest,
    UpdateServiceRequest,
)
from app.modules.catalog.service import (
    CatalogService,
    OfferedService,
    ServiceSummary,
    build_catalog_service,
)
from app.modules.users.actor import Actor, ForbiddenError
from app.modules.users.models import User, UserRole
from tests.conftest import UNKNOWN_ID, FakeClock, ServiceAssigner, ServiceFactory, UserFactory


@pytest.fixture
def catalog(db_session: AsyncSession, clock: FakeClock) -> CatalogService:
    return build_catalog_service(db_session, clock)


async def test_offered_service_exposes_the_catalog_fields_at_the_list_price(
    catalog: CatalogService,
    therapist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    service = await make_service(
        name="Online Session",
        description="Details",
        duration_minutes=45,
        price_amount=Decimal("1200.00"),
        currency="EUR",
        delivery_mode=DeliveryMode.ONLINE,
    )
    await assign_service(therapist, service)

    offered = await catalog.get_offered_service(therapist.id, service.id)

    assert offered == OfferedService(
        id=service.id,
        name="Online Session",
        description="Details",
        duration_minutes=45,
        price_amount=Decimal("1200.00"),
        currency="EUR",
        delivery_mode=DeliveryMode.ONLINE,
    )


@pytest.mark.parametrize("custom_price", [Decimal("750.00"), Decimal("0.00")])
async def test_custom_price_replaces_the_list_price(
    catalog: CatalogService,
    therapist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
    custom_price: Decimal,
) -> None:
    service = await make_service()
    await assign_service(therapist, service, custom_price)

    offered = await catalog.get_offered_service(therapist.id, service.id)

    assert offered is not None
    assert offered.price_amount == custom_price


async def test_offered_service_cannot_be_changed(
    catalog: CatalogService,
    therapist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    service = await make_service()
    await assign_service(therapist, service)
    offered = await catalog.get_offered_service(therapist.id, service.id)

    field = "price_amount"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(offered, field, Decimal("1.00"))


async def test_inactive_service_is_not_offered(
    catalog: CatalogService,
    therapist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    service = await make_service(is_active=False)
    await assign_service(therapist, service)

    assert await catalog.get_offered_service(therapist.id, service.id) is None
    assert await catalog.list_offered_services(therapist.id) == []


async def test_service_of_a_colleague_is_not_offered(
    catalog: CatalogService,
    therapist: User,
    make_user: UserFactory,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    service = await make_service()
    await assign_service(await make_user(UserRole.PSYCHOLOGIST), service)

    assert await catalog.get_offered_service(therapist.id, service.id) is None
    assert await catalog.list_offered_services(therapist.id) == []


async def test_unknown_service_is_not_offered(catalog: CatalogService, therapist: User) -> None:
    assert await catalog.get_offered_service(therapist.id, UNKNOWN_ID) is None


async def test_offered_services_are_listed_by_name_at_effective_prices(
    catalog: CatalogService,
    therapist: User,
    make_service: ServiceFactory,
    assign_service: ServiceAssigner,
) -> None:
    await assign_service(therapist, await make_service(name="B"), Decimal("10.00"))
    await assign_service(therapist, await make_service(name="A"))

    offered = await catalog.list_offered_services(therapist.id)

    assert [(service.name, service.price_amount) for service in offered] == [
        ("A", Decimal("1500.00")),
        ("B", Decimal("10.00")),
    ]


async def test_summaries_include_inactive_services_and_skip_unknown_ids(
    catalog: CatalogService, make_service: ServiceFactory
) -> None:
    active = await make_service(name="Active", delivery_mode=DeliveryMode.ONLINE)
    inactive = await make_service(name="Inactive", is_active=False)

    summaries = await catalog.get_service_summaries([active.id, inactive.id, UNKNOWN_ID])

    assert summaries == {
        active.id: ServiceSummary(name="Active", delivery_mode=DeliveryMode.ONLINE),
        inactive.id: ServiceSummary(name="Inactive", delivery_mode=DeliveryMode.IN_PERSON),
    }


async def test_summaries_of_no_ids_are_empty(catalog: CatalogService) -> None:
    assert await catalog.get_service_summaries([]) == {}


@pytest.mark.parametrize("role", [UserRole.PSYCHOLOGIST, UserRole.CLIENT])
async def test_admin_methods_enforce_the_role_without_a_router(
    catalog: CatalogService, role: UserRole
) -> None:
    actor = Actor(user_id=uuid.uuid4(), role=role)
    request = CreateServiceRequest(
        name="Name",
        duration_minutes=50,
        price_amount=Decimal("1.00"),
        delivery_mode=DeliveryMode.ONLINE,
    )

    assignment = AssignServiceRequest()

    calls = [
        catalog.list_services(actor, PageParams()),
        catalog.get_service(actor, UNKNOWN_ID),
        catalog.create_service(actor, request, ip_address=None),
        catalog.update_service(actor, UNKNOWN_ID, UpdateServiceRequest(), ip_address=None),
        catalog.deactivate_service(actor, UNKNOWN_ID, ip_address=None),
        catalog.list_assignments(actor, UNKNOWN_ID, PageParams()),
        catalog.assign_service(actor, UNKNOWN_ID, UNKNOWN_ID, assignment, ip_address=None),
        catalog.unassign_service(actor, UNKNOWN_ID, UNKNOWN_ID, ip_address=None),
    ]

    for call in calls:
        with pytest.raises(ForbiddenError):
            await call


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
async def test_own_services_require_the_psychologist_role_without_a_router(
    catalog: CatalogService, role: UserRole
) -> None:
    with pytest.raises(ForbiddenError):
        await catalog.list_own_services(Actor(user_id=uuid.uuid4(), role=role), PageParams())
