import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.database import WriteSession
from app.core.pagination import Page, PageParamsDep
from app.modules.auth.dependencies import (
    ClockDep,
    PsychologistActor,
    RequestMetaDep,
    SettingsDep,
    StaffActor,
)
from app.modules.clients.schemas import (
    ClientResponse,
    ClientSearch,
    CreateClientRequest,
    UpdateClientRequest,
)
from app.modules.clients.service import AppointmentLookup, ClientService, build_client_service
from app.modules.scheduling.service import build_appointment_lookup

router = APIRouter(prefix="/staff/clients", tags=["clients"])


def get_client_service(
    session: WriteSession, settings: SettingsDep, clock: ClockDep
) -> ClientService:
    return build_client_service(session, settings, clock)


def get_appointment_lookup(session: WriteSession, clock: ClockDep) -> AppointmentLookup:
    return build_appointment_lookup(session, clock)


ClientServiceDep = Annotated[ClientService, Depends(get_client_service)]
AppointmentLookupDep = Annotated[AppointmentLookup, Depends(get_appointment_lookup)]


@router.get("")
async def list_clients(
    actor: StaffActor,
    clients: ClientServiceDep,
    page: PageParamsDep,
    search: Annotated[ClientSearch | None, Query()] = None,
) -> Page[ClientResponse]:
    found, total = await clients.list_clients(actor, page, search)
    return Page[ClientResponse].of(
        [ClientResponse.model_validate(client) for client in found], total, page
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_client(
    body: CreateClientRequest,
    actor: PsychologistActor,
    clients: ClientServiceDep,
    meta: RequestMetaDep,
) -> ClientResponse:
    client = await clients.create_client(actor, body, ip_address=meta.ip_address)
    return ClientResponse.model_validate(client)


@router.get("/{client_id}")
async def get_client(
    client_id: uuid.UUID, actor: StaffActor, clients: ClientServiceDep
) -> ClientResponse:
    return ClientResponse.model_validate(await clients.get_client(actor, client_id))


@router.patch("/{client_id}")
async def update_client(
    client_id: uuid.UUID,
    body: UpdateClientRequest,
    actor: PsychologistActor,
    clients: ClientServiceDep,
    meta: RequestMetaDep,
) -> ClientResponse:
    client = await clients.update_client(actor, client_id, body, ip_address=meta.ip_address)
    return ClientResponse.model_validate(client)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_care(
    client_id: uuid.UUID,
    actor: PsychologistActor,
    clients: ClientServiceDep,
    appointments: AppointmentLookupDep,
    meta: RequestMetaDep,
) -> None:
    await clients.end_care(actor, client_id, appointments, ip_address=meta.ip_address)
