"""Client records and the care relationships that give psychologists access to them.

Methods that take an actor enforce its role and scope. The others serve other modules,
which authorize their own callers.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.core.models import changed_values
from app.core.pagination import PageParams
from app.modules.audit.service import AuditAction, AuditService, build_audit_service
from app.modules.clients.models import Client
from app.modules.clients.repository import ClientRepository
from app.modules.clients.schemas import CreateClientRequest, UpdateClientRequest
from app.modules.consent.service import ConsentService, build_consent_service
from app.modules.users.actor import STAFF_ROLES, Actor
from app.modules.users.models import UserRole

CLIENT_ENTITY = "client"


class InvalidBirthDateError(AppError):
    status_code = 422
    code = "INVALID_BIRTH_DATE"
    message = "The birth date cannot be in the future."


class ClientHasActiveAppointmentsError(AppError):
    status_code = 409
    code = "CLIENT_HAS_ACTIVE_APPOINTMENTS"
    message = "This client has upcoming appointments."


class AppointmentLookup(Protocol):
    """What ending care asks of scheduling, a module that depends on this one."""

    async def has_upcoming_appointments(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> bool: ...


@dataclass(frozen=True)
class ClientName:
    first_name: str
    last_name: str


class ClientService:
    def __init__(
        self,
        repository: ClientRepository,
        consents: ConsentService,
        audit: AuditService,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._consents = consents
        self._audit = audit
        self._settings = settings
        self._clock = clock

    async def list_clients(
        self, actor: Actor, page: PageParams, search: str | None = None
    ) -> tuple[Sequence[Client], int]:
        actor.require_role(*STAFF_ROLES)
        scope = self._scope(actor)
        terms = search.split() if search else []
        clients = await self._repository.list_visible(scope, terms, page.limit, page.offset)
        return clients, await self._repository.count_visible(scope, terms)

    async def get_client(self, actor: Actor, client_id: uuid.UUID) -> Client:
        actor.require_role(*STAFF_ROLES)
        return await self._require_visible(self._scope(actor), client_id)

    async def create_client(
        self, actor: Actor, request: CreateClientRequest, *, ip_address: str | None
    ) -> Client:
        """Register a client without a login account, under the actor's care."""
        actor.require_role(UserRole.PSYCHOLOGIST)
        self._ensure_valid_birth_date(request.birth_date)
        client = Client(
            **request.model_dump(exclude={"kvkk_disclosure_given"}),
            created_by_user_id=actor.user_id,
        )
        await self._repository.add(client)
        await self._repository.upsert_relationship(actor.user_id, client.id)
        await self._consents.record_kvkk_disclosure(
            client.id, self._settings.kvkk_disclosure_version, recorded_by_user_id=actor.user_id
        )
        await self._record(AuditAction.CLIENT_CREATED, actor, client.id, ip_address)
        return client

    async def update_client(
        self,
        actor: Actor,
        client_id: uuid.UUID,
        request: UpdateClientRequest,
        *,
        ip_address: str | None,
    ) -> Client:
        actor.require_role(UserRole.PSYCHOLOGIST)
        client = await self._require_visible(actor.user_id, client_id)
        self._ensure_valid_birth_date(request.birth_date)
        changes = changed_values(client, request.model_dump(exclude_unset=True))
        if changes:
            for name, value in changes.items():
                setattr(client, name, value)
            await self._repository.flush()
            await self._record(
                AuditAction.CLIENT_UPDATED,
                actor,
                client.id,
                ip_address,
                {"changed_fields": sorted(changes)},
            )
        return client

    async def end_care(
        self,
        actor: Actor,
        client_id: uuid.UUID,
        appointments: AppointmentLookup,
        *,
        ip_address: str | None,
    ) -> None:
        """End the actor's care of a client, and soft-delete a client left with no one.

        `appointments` is an argument, because registration builds this service without
        a scheduling module, and a lookup that is missing must not skip the check.

        The lock comes first, so that every check below sees what parallel requests
        committed. It also waits for a booking in flight, as `lock` explains.
        """
        actor.require_role(UserRole.PSYCHOLOGIST)
        client = await self._repository.lock(client_id, allow_references=False)
        relationship = await self._repository.get_active_relationship(actor.user_id, client_id)
        if client is None or client.deleted_at is not None or relationship is None:
            raise NotFoundError
        if await appointments.has_upcoming_appointments(actor.user_id, client_id):
            raise ClientHasActiveAppointmentsError

        now = self._clock()
        relationship.ended_at = now
        await self._repository.flush()
        client_deleted = not await self._repository.has_active_relationship(client_id)
        if client_deleted:
            client.deleted_at = now
            await self._repository.flush()
        await self._record(
            AuditAction.CLIENT_CARE_ENDED,
            actor,
            client_id,
            ip_address,
            {"client_deleted": client_deleted},
        )

    async def create_for_registered_user(
        self, user_id: uuid.UUID, first_name: str, last_name: str, email: str, phone: str
    ) -> Client:
        client = Client(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            created_by_user_id=user_id,
        )
        await self._repository.add(client)
        return client

    async def get_for_user(self, user_id: uuid.UUID) -> Client | None:
        """Return the record of a login account, even when care ended and it is soft-deleted.

        Such a client can still log in, see their history and book again.
        """
        return await self._repository.get_by_user(user_id)

    async def ensure_care_relationship(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> None:
        """Open or reopen a care relationship, and restore a client that was soft-deleted.

        The lock order matches `end_care`, which would otherwise deadlock with this method.
        An appointment insert has usually left a key share lock on the row, and references
        stay allowed so that two such bookings of one client do not deadlock either.
        """
        client = await self._repository.lock(client_id, allow_references=True)
        if client is None:
            raise NotFoundError
        await self._repository.upsert_relationship(psychologist_id, client_id)
        if client.deleted_at is not None:
            client.deleted_at = None
            await self._repository.flush()
            # No actor here: the caller's entry for the appointment names who booked.
            await self._audit.record(
                AuditAction.CLIENT_UPDATED,
                actor_user_id=None,
                entity_type=CLIENT_ENTITY,
                entity_id=client_id,
                metadata={"changed_fields": ["deleted_at"]},
            )

    async def has_care_relationship_row(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> bool:
        """Tell whether the two ever had a care relationship, active or ended."""
        return await self._repository.has_relationship_row(psychologist_id, client_id)

    async def get_display_names(self, ids: Collection[uuid.UUID]) -> Mapping[uuid.UUID, ClientName]:
        """Look up names in batch, including deleted clients that history still shows."""
        return {
            client_id: ClientName(first_name=first_name, last_name=last_name)
            for client_id, first_name, last_name in await self._repository.get_names(ids)
        }

    @staticmethod
    def _scope(actor: Actor) -> uuid.UUID | None:
        """Return the psychologist whose clients the actor sees, or None for all clients."""
        return None if actor.role is UserRole.ADMIN else actor.user_id

    async def _require_visible(
        self, psychologist_id: uuid.UUID | None, client_id: uuid.UUID
    ) -> Client:
        client = await self._repository.get_visible(psychologist_id, client_id)
        if client is None:
            raise NotFoundError
        return client

    def _ensure_valid_birth_date(self, birth_date: date | None) -> None:
        if birth_date is not None and birth_date > self._clock().date():
            raise InvalidBirthDateError

    async def _record(
        self,
        action: AuditAction,
        actor: Actor,
        client_id: uuid.UUID,
        ip_address: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            action,
            actor_user_id=actor.user_id,
            entity_type=CLIENT_ENTITY,
            entity_id=client_id,
            ip_address=ip_address,
            metadata=metadata,
        )


def build_client_service(session: AsyncSession, settings: Settings, clock: Clock) -> ClientService:
    return ClientService(
        ClientRepository(session),
        build_consent_service(session),
        build_audit_service(session),
        settings,
        clock,
    )
