import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.database import WriteSession
from app.core.pagination import Page, PageParamsDep
from app.modules.auth.dependencies import (
    AdminActor,
    ClockDep,
    PsychologistActor,
    RequestMetaDep,
)
from app.modules.catalog.schemas import (
    AssignedServiceResponse,
    AssignServiceRequest,
    CreateServiceRequest,
    OfferedServiceResponse,
    ServiceResponse,
    UpdateServiceRequest,
)
from app.modules.catalog.service import CatalogService, build_catalog_service

router = APIRouter(tags=["catalog"])


def get_catalog_service(session: WriteSession, clock: ClockDep) -> CatalogService:
    return build_catalog_service(session, clock)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]


@router.get("/admin/services")
async def list_services(
    actor: AdminActor, catalog: CatalogServiceDep, page: PageParamsDep
) -> Page[ServiceResponse]:
    services, total = await catalog.list_services(actor, page)
    return Page[ServiceResponse].of(
        [ServiceResponse.model_validate(service) for service in services], total, page
    )


@router.post("/admin/services", status_code=status.HTTP_201_CREATED)
async def create_service(
    body: CreateServiceRequest,
    actor: AdminActor,
    catalog: CatalogServiceDep,
    meta: RequestMetaDep,
) -> ServiceResponse:
    service = await catalog.create_service(actor, body, ip_address=meta.ip_address)
    return ServiceResponse.model_validate(service)


@router.get("/admin/services/{service_id}")
async def get_service(
    service_id: uuid.UUID, actor: AdminActor, catalog: CatalogServiceDep
) -> ServiceResponse:
    return ServiceResponse.model_validate(await catalog.get_service(actor, service_id))


@router.patch("/admin/services/{service_id}")
async def update_service(
    service_id: uuid.UUID,
    body: UpdateServiceRequest,
    actor: AdminActor,
    catalog: CatalogServiceDep,
    meta: RequestMetaDep,
) -> ServiceResponse:
    service = await catalog.update_service(actor, service_id, body, ip_address=meta.ip_address)
    return ServiceResponse.model_validate(service)


@router.delete("/admin/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_service(
    service_id: uuid.UUID, actor: AdminActor, catalog: CatalogServiceDep, meta: RequestMetaDep
) -> None:
    await catalog.deactivate_service(actor, service_id, ip_address=meta.ip_address)


@router.get("/admin/psychologists/{psychologist_id}/services")
async def list_assignments(
    psychologist_id: uuid.UUID,
    actor: AdminActor,
    catalog: CatalogServiceDep,
    page: PageParamsDep,
) -> Page[AssignedServiceResponse]:
    assigned, total = await catalog.list_assignments(actor, psychologist_id, page)
    return Page[AssignedServiceResponse].of(
        [AssignedServiceResponse.model_validate(service) for service in assigned], total, page
    )


@router.put("/admin/psychologists/{psychologist_id}/services/{service_id}")
async def assign_service(
    psychologist_id: uuid.UUID,
    service_id: uuid.UUID,
    body: AssignServiceRequest,
    actor: AdminActor,
    catalog: CatalogServiceDep,
    meta: RequestMetaDep,
) -> AssignedServiceResponse:
    assigned = await catalog.assign_service(
        actor, psychologist_id, service_id, body, ip_address=meta.ip_address
    )
    return AssignedServiceResponse.model_validate(assigned)


@router.delete(
    "/admin/psychologists/{psychologist_id}/services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unassign_service(
    psychologist_id: uuid.UUID,
    service_id: uuid.UUID,
    actor: AdminActor,
    catalog: CatalogServiceDep,
    meta: RequestMetaDep,
) -> None:
    await catalog.unassign_service(actor, psychologist_id, service_id, ip_address=meta.ip_address)


@router.get("/staff/services")
async def list_own_services(
    actor: PsychologistActor, catalog: CatalogServiceDep, page: PageParamsDep
) -> Page[OfferedServiceResponse]:
    offered, total = await catalog.list_own_services(actor, page)
    return Page[OfferedServiceResponse].of(
        [OfferedServiceResponse.model_validate(service) for service in offered], total, page
    )
