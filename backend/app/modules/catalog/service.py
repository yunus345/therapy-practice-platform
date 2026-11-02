"""The therapy services on offer and which psychologist offers them at what price.

Methods that take an actor enforce its role. The others return catalog data for other
modules, which authorize their own callers.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import NotFoundError
from app.core.models import changed_values
from app.core.pagination import PageParams
from app.modules.audit.service import AuditAction, AuditService, build_audit_service
from app.modules.catalog.models import DeliveryMode, Service
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.schemas import (
    AssignServiceRequest,
    CreateServiceRequest,
    UpdateServiceRequest,
)
from app.modules.users.actor import Actor
from app.modules.users.models import UserRole
from app.modules.users.service import UserService, build_user_service

SERVICE_ENTITY = "service"


@dataclass(frozen=True)
class OfferedService:
    """A service as one psychologist offers it: `price_amount` is the effective price."""

    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    price_amount: Decimal
    currency: str
    delivery_mode: DeliveryMode


@dataclass(frozen=True)
class AssignedService(OfferedService):
    custom_price_amount: Decimal | None
    is_active: bool


@dataclass(frozen=True)
class ServiceSummary:
    name: str
    delivery_mode: DeliveryMode


def _offered(service: Service, custom_price: Decimal | None) -> OfferedService:
    return OfferedService(
        id=service.id,
        name=service.name,
        description=service.description,
        duration_minutes=service.duration_minutes,
        # A custom price of zero is valid, so only None falls back to the list price.
        price_amount=service.price_amount if custom_price is None else custom_price,
        currency=service.currency,
        delivery_mode=service.delivery_mode,
    )


def _assigned(service: Service, custom_price: Decimal | None) -> AssignedService:
    return AssignedService(
        **asdict(_offered(service, custom_price)),
        custom_price_amount=custom_price,
        is_active=service.is_active,
    )


class CatalogService:
    def __init__(
        self, repository: CatalogRepository, users: UserService, audit: AuditService
    ) -> None:
        self._repository = repository
        self._users = users
        self._audit = audit

    async def list_services(self, actor: Actor, page: PageParams) -> tuple[Sequence[Service], int]:
        actor.require_role(UserRole.ADMIN)
        services = await self._repository.list_services(page.limit, page.offset)
        return services, await self._repository.count_services()

    async def get_service(self, actor: Actor, service_id: uuid.UUID) -> Service:
        actor.require_role(UserRole.ADMIN)
        return await self._require_service(service_id)

    async def create_service(
        self, actor: Actor, request: CreateServiceRequest, *, ip_address: str | None
    ) -> Service:
        actor.require_role(UserRole.ADMIN)
        service = Service(**request.model_dump())
        await self._repository.add_service(service)
        await self._record(AuditAction.SERVICE_CREATED, actor, service.id, ip_address)
        return service

    async def update_service(
        self,
        actor: Actor,
        service_id: uuid.UUID,
        request: UpdateServiceRequest,
        *,
        ip_address: str | None,
    ) -> Service:
        actor.require_role(UserRole.ADMIN)
        service = await self._require_service(service_id)
        changes = changed_values(service, request.model_dump(exclude_unset=True))
        if changes:
            for name, value in changes.items():
                setattr(service, name, value)
            await self._repository.flush()
            await self._record(
                AuditAction.SERVICE_UPDATED,
                actor,
                service.id,
                ip_address,
                {"changed_fields": sorted(changes)},
            )
        return service

    async def deactivate_service(
        self, actor: Actor, service_id: uuid.UUID, *, ip_address: str | None
    ) -> None:
        """Hide a service from new bookings. Appointments keep referencing the row."""
        actor.require_role(UserRole.ADMIN)
        service = await self._require_service(service_id)
        if service.is_active:
            service.is_active = False
            await self._repository.flush()
            await self._record(AuditAction.SERVICE_DEACTIVATED, actor, service.id, ip_address)

    async def list_assignments(
        self, actor: Actor, psychologist_id: uuid.UUID, page: PageParams
    ) -> tuple[list[AssignedService], int]:
        actor.require_role(UserRole.ADMIN)
        await self._require_psychologist(psychologist_id)
        rows = await self._repository.list_assigned(
            psychologist_id, active_only=False, limit=page.limit, offset=page.offset
        )
        total = await self._repository.count_assigned(psychologist_id, active_only=False)
        return [_assigned(*row) for row in rows], total

    async def assign_service(
        self,
        actor: Actor,
        psychologist_id: uuid.UUID,
        service_id: uuid.UUID,
        request: AssignServiceRequest,
        *,
        ip_address: str | None,
    ) -> AssignedService:
        """Assign a service or replace the custom price of an existing assignment."""
        actor.require_role(UserRole.ADMIN)
        await self._require_psychologist(psychologist_id)
        service = await self._require_service(service_id)
        custom_price = request.custom_price_amount
        await self._repository.upsert_assignment(psychologist_id, service_id, custom_price)
        await self._record(
            AuditAction.SERVICE_ASSIGNED,
            actor,
            service_id,
            ip_address,
            {"psychologist_id": str(psychologist_id)},
        )
        return _assigned(service, custom_price)

    async def unassign_service(
        self,
        actor: Actor,
        psychologist_id: uuid.UUID,
        service_id: uuid.UUID,
        *,
        ip_address: str | None,
    ) -> None:
        actor.require_role(UserRole.ADMIN)
        if not await self._repository.delete_assignment(psychologist_id, service_id):
            raise NotFoundError
        await self._record(
            AuditAction.SERVICE_UNASSIGNED,
            actor,
            service_id,
            ip_address,
            {"psychologist_id": str(psychologist_id)},
        )

    async def list_own_services(
        self, actor: Actor, page: PageParams
    ) -> tuple[list[OfferedService], int]:
        actor.require_role(UserRole.PSYCHOLOGIST)
        rows = await self._repository.list_assigned(
            actor.user_id, active_only=True, limit=page.limit, offset=page.offset
        )
        total = await self._repository.count_assigned(actor.user_id, active_only=True)
        return [_offered(*row) for row in rows], total

    async def get_offered_service(
        self, psychologist_id: uuid.UUID, service_id: uuid.UUID
    ) -> OfferedService | None:
        """Return a service only if the psychologist can be booked for it right now."""
        row = await self._repository.get_offered(psychologist_id, service_id)
        return _offered(*row) if row else None

    async def list_offered_services(self, psychologist_id: uuid.UUID) -> list[OfferedService]:
        rows = await self._repository.list_assigned(psychologist_id, active_only=True)
        return [_offered(*row) for row in rows]

    async def get_service_summaries(
        self, ids: Collection[uuid.UUID]
    ) -> Mapping[uuid.UUID, ServiceSummary]:
        """Look up names in batch, including inactive services that history still shows."""
        return {
            service_id: ServiceSummary(name=name, delivery_mode=delivery_mode)
            for service_id, name, delivery_mode in await self._repository.get_summaries(ids)
        }

    async def _require_service(self, service_id: uuid.UUID) -> Service:
        service = await self._repository.get_service(service_id)
        if service is None:
            raise NotFoundError
        return service

    async def _require_psychologist(self, psychologist_id: uuid.UUID) -> None:
        if await self._users.get_profile(psychologist_id) is None:
            raise NotFoundError

    async def _record(
        self,
        action: AuditAction,
        actor: Actor,
        service_id: uuid.UUID,
        ip_address: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            action,
            actor_user_id=actor.user_id,
            entity_type=SERVICE_ENTITY,
            entity_id=service_id,
            ip_address=ip_address,
            metadata=metadata,
        )


def build_catalog_service(session: AsyncSession, clock: Clock) -> CatalogService:
    return CatalogService(
        CatalogRepository(session), build_user_service(session, clock), build_audit_service(session)
    )
