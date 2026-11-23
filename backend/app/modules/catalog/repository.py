import uuid
from collections.abc import Collection, Sequence
from decimal import Decimal

from sqlalchemy import Row, Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import DeliveryMode, PsychologistService, Service

type AssignedRow = Row[Service, Decimal | None]


def _assigned(psychologist_id: uuid.UUID, *, active_only: bool) -> Select[Service, Decimal | None]:
    statement = (
        select(Service, PsychologistService.custom_price_amount)
        .join(PsychologistService, PsychologistService.service_id == Service.id)
        .where(PsychologistService.psychologist_id == psychologist_id)
    )
    return statement.where(Service.is_active.is_(True)) if active_only else statement


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_service(self, service: Service) -> None:
        self._session.add(service)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()

    async def get_service(self, service_id: uuid.UUID) -> Service | None:
        return await self._session.get(Service, service_id, populate_existing=True)

    async def list_services(self, limit: int, offset: int) -> Sequence[Service]:
        result = await self._session.scalars(
            select(Service).order_by(Service.name, Service.id).limit(limit).offset(offset)
        )
        return result.all()

    async def count_services(self) -> int:
        return await self._session.scalar(select(func.count()).select_from(Service)) or 0

    async def get_summaries(
        self, ids: Collection[uuid.UUID]
    ) -> Sequence[Row[uuid.UUID, str, DeliveryMode]]:
        result = await self._session.execute(
            select(Service.id, Service.name, Service.delivery_mode).where(Service.id.in_(ids))
        )
        return result.all()

    async def get_offered(
        self, psychologist_id: uuid.UUID, service_id: uuid.UUID
    ) -> AssignedRow | None:
        result = await self._session.execute(
            _assigned(psychologist_id, active_only=True).where(Service.id == service_id)
        )
        return result.one_or_none()

    async def list_assigned(
        self,
        psychologist_id: uuid.UUID,
        *,
        active_only: bool,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[AssignedRow]:
        result = await self._session.execute(
            _assigned(psychologist_id, active_only=active_only)
            .order_by(Service.name, Service.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    async def count_assigned(self, psychologist_id: uuid.UUID, *, active_only: bool) -> int:
        assigned = _assigned(psychologist_id, active_only=active_only).subquery()
        return await self._session.scalar(select(func.count()).select_from(assigned)) or 0

    async def upsert_assignment(
        self, psychologist_id: uuid.UUID, service_id: uuid.UUID, custom_price: Decimal | None
    ) -> None:
        statement = insert(PsychologistService).values(
            psychologist_id=psychologist_id,
            service_id=service_id,
            custom_price_amount=custom_price,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["psychologist_id", "service_id"],
                set_={"custom_price_amount": statement.excluded.custom_price_amount},
            )
        )

    async def delete_assignment(self, psychologist_id: uuid.UUID, service_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(PsychologistService)
            .where(
                PsychologistService.psychologist_id == psychologist_id,
                PsychologistService.service_id == service_id,
            )
            .returning(PsychologistService.id)
        )
        return result.scalar_one_or_none() is not None
