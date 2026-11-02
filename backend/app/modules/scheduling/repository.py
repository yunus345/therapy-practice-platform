import uuid
from collections.abc import Collection, Sequence
from datetime import date, datetime

from sqlalchemy import ColumnElement, Row, Select, and_, delete, func, not_, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.models import (
    ACTIVE_STATUSES,
    NO_OVERLAP_CONSTRAINT,
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    AppointmentWhen,
    AvailabilityRule,
    TimeOff,
)

EXCLUSION_VIOLATION = "23P01"
DEADLOCK_DETECTED = "40P01"


def _active_appointments_between(
    psychologist_id: uuid.UUID, start: datetime, end: datetime
) -> tuple[ColumnElement[bool], ...]:
    return (
        Appointment.psychologist_id == psychologist_id,
        Appointment.status.in_(ACTIVE_STATUSES),
        Appointment.starts_at < end,
        Appointment.ends_at > start,
    )


def _upcoming(now: datetime) -> ColumnElement[bool]:
    return and_(Appointment.ends_at > now, Appointment.status.in_(ACTIVE_STATUSES))


def _client_appointments(
    client_id: uuid.UUID, when: AppointmentWhen, now: datetime
) -> Select[Appointment]:
    statement = select(Appointment).where(Appointment.client_id == client_id)
    if when is AppointmentWhen.UPCOMING:
        return statement.where(_upcoming(now))
    if when is AppointmentWhen.PAST:
        return statement.where(not_(_upcoming(now)))
    return statement


def _staff_appointments(
    psychologist_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    statuses: Collection[AppointmentStatus],
) -> Select[Appointment]:
    """Appointments that overlap the bounds that are given. No psychologist means all."""
    statement = select(Appointment)
    if psychologist_id is not None:
        statement = statement.where(Appointment.psychologist_id == psychologist_id)
    if start is not None:
        statement = statement.where(Appointment.ends_at > start)
    if end is not None:
        statement = statement.where(Appointment.starts_at < end)
    if statuses:
        statement = statement.where(Appointment.status.in_(statuses))
    return statement


def _lost_the_slot(error: DBAPIError) -> bool:
    """Tell whether a failed insert means that another appointment took the slot."""
    sqlstate = getattr(error.orig, "sqlstate", None)
    return sqlstate == DEADLOCK_DETECTED or (
        sqlstate == EXCLUSION_VIOLATION and NO_OVERLAP_CONSTRAINT in str(error.orig)
    )


def _time_off(
    psychologist_id: uuid.UUID, start: datetime | None, end: datetime | None
) -> Select[TimeOff]:
    """Periods of a psychologist that overlap the bounds that are given."""
    statement = select(TimeOff).where(TimeOff.psychologist_id == psychologist_id)
    if start is not None:
        statement = statement.where(TimeOff.ends_at > start)
    if end is not None:
        statement = statement.where(TimeOff.starts_at < end)
    return statement


class SchedulingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: AvailabilityRule | TimeOff | AppointmentStatusHistory) -> None:
        self._session.add(row)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()

    async def insert_appointment(self, appointment: Appointment) -> bool:
        """Insert an appointment, returning False if it lost a race for the slot.

        Two parallel inserts into the exclusion index can wait for each other, and Postgres
        then aborts one of them as a deadlock. That is one more way of losing the race. The
        savepoint keeps the transaction usable after either failure.
        """
        try:
            async with self._session.begin_nested():
                self._session.add(appointment)
                await self._session.flush()
        except DBAPIError as error:
            if _lost_the_slot(error):
                return False
            raise
        return True

    async def get_appointment(
        self, psychologist_id: uuid.UUID | None, appointment_id: uuid.UUID
    ) -> Appointment | None:
        return await self._session.scalar(
            _staff_appointments(psychologist_id, None, None, ()).where(
                Appointment.id == appointment_id
            )
        )

    async def lock_client_appointment(
        self, client_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> Appointment | None:
        return await self._lock_appointment(
            Appointment.id == appointment_id, Appointment.client_id == client_id
        )

    async def lock_psychologist_appointment(
        self, psychologist_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> Appointment | None:
        return await self._lock_appointment(
            Appointment.id == appointment_id, Appointment.psychologist_id == psychologist_id
        )

    async def _lock_appointment(self, *conditions: ColumnElement[bool]) -> Appointment | None:
        """Read the row under a lock, so that parallel status changes run one at a time."""
        return await self._session.scalar(
            select(Appointment)
            .where(*conditions)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_client_appointments(
        self,
        client_id: uuid.UUID,
        when: AppointmentWhen,
        now: datetime,
        limit: int,
        offset: int,
    ) -> Sequence[Appointment]:
        order = (
            (Appointment.starts_at, Appointment.id)
            if when is AppointmentWhen.UPCOMING
            else (Appointment.starts_at.desc(), Appointment.id.desc())
        )
        result = await self._session.scalars(
            _client_appointments(client_id, when, now).order_by(*order).limit(limit).offset(offset)
        )
        return result.all()

    async def count_client_appointments(
        self, client_id: uuid.UUID, when: AppointmentWhen, now: datetime
    ) -> int:
        appointments = _client_appointments(client_id, when, now).subquery()
        return await self._session.scalar(select(func.count()).select_from(appointments)) or 0

    async def list_staff_appointments(
        self,
        psychologist_id: uuid.UUID | None,
        start: datetime | None,
        end: datetime | None,
        statuses: Collection[AppointmentStatus],
        limit: int,
        offset: int,
    ) -> Sequence[Appointment]:
        result = await self._session.scalars(
            _staff_appointments(psychologist_id, start, end, statuses)
            .order_by(Appointment.starts_at, Appointment.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    async def count_staff_appointments(
        self,
        psychologist_id: uuid.UUID | None,
        start: datetime | None,
        end: datetime | None,
        statuses: Collection[AppointmentStatus],
    ) -> int:
        appointments = _staff_appointments(psychologist_id, start, end, statuses).subquery()
        return await self._session.scalar(select(func.count()).select_from(appointments)) or 0

    async def has_upcoming_appointments(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID, now: datetime
    ) -> bool:
        statement = (
            select(Appointment.id)
            .where(
                Appointment.psychologist_id == psychologist_id,
                Appointment.client_id == client_id,
                Appointment.status.in_(ACTIVE_STATUSES),
                Appointment.ends_at > now,
            )
            .limit(1)
        )
        return await self._session.scalar(statement) is not None

    async def count_active_appointments(
        self, psychologist_id: uuid.UUID, start: datetime, end: datetime
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Appointment)
            .where(*_active_appointments_between(psychologist_id, start, end))
        )
        return await self._session.scalar(statement) or 0

    async def appointment_ranges(
        self, psychologist_id: uuid.UUID, start: datetime, end: datetime
    ) -> Sequence[Row[datetime, datetime]]:
        """Read bare time ranges, so that nothing about clients can reach slot calculation."""
        result = await self._session.execute(
            select(Appointment.starts_at, Appointment.ends_at).where(
                *_active_appointments_between(psychologist_id, start, end)
            )
        )
        return result.all()

    async def list_rules(
        self, psychologist_id: uuid.UUID, limit: int, offset: int
    ) -> Sequence[AvailabilityRule]:
        result = await self._session.scalars(
            select(AvailabilityRule)
            .where(AvailabilityRule.psychologist_id == psychologist_id)
            .order_by(
                AvailabilityRule.weekday,
                AvailabilityRule.start_time,
                AvailabilityRule.valid_from,
                AvailabilityRule.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    async def count_rules(self, psychologist_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(AvailabilityRule)
            .where(AvailabilityRule.psychologist_id == psychologist_id)
        )
        return await self._session.scalar(statement) or 0

    async def rules_in_effect(
        self, psychologist_id: uuid.UUID, from_date: date, to_date: date
    ) -> Sequence[AvailabilityRule]:
        result = await self._session.scalars(
            select(AvailabilityRule).where(
                AvailabilityRule.psychologist_id == psychologist_id,
                AvailabilityRule.valid_from <= to_date,
                or_(
                    AvailabilityRule.valid_until.is_(None),
                    AvailabilityRule.valid_until >= from_date,
                ),
            )
        )
        return result.all()

    async def lock_rule(
        self, psychologist_id: uuid.UUID, rule_id: uuid.UUID
    ) -> AvailabilityRule | None:
        """Read the row under a lock, so that two partial updates cannot break its checks."""
        return await self._session.scalar(
            select(AvailabilityRule)
            .where(
                AvailabilityRule.id == rule_id,
                AvailabilityRule.psychologist_id == psychologist_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def delete_rule(self, psychologist_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(AvailabilityRule)
            .where(
                AvailabilityRule.id == rule_id,
                AvailabilityRule.psychologist_id == psychologist_id,
            )
            .returning(AvailabilityRule.id)
        )
        return result.scalar_one_or_none() is not None

    async def list_time_off(
        self,
        psychologist_id: uuid.UUID,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> Sequence[TimeOff]:
        result = await self._session.scalars(
            _time_off(psychologist_id, start, end)
            .order_by(TimeOff.starts_at, TimeOff.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    async def count_time_off(
        self, psychologist_id: uuid.UUID, start: datetime | None, end: datetime | None
    ) -> int:
        periods = _time_off(psychologist_id, start, end).subquery()
        return await self._session.scalar(select(func.count()).select_from(periods)) or 0

    async def time_off_ranges(
        self, psychologist_id: uuid.UUID, start: datetime, end: datetime
    ) -> Sequence[Row[datetime, datetime]]:
        """Read bare time ranges, so that no reason can reach slot calculation."""
        result = await self._session.execute(
            select(TimeOff.starts_at, TimeOff.ends_at).where(
                TimeOff.psychologist_id == psychologist_id,
                TimeOff.starts_at < end,
                TimeOff.ends_at > start,
            )
        )
        return result.all()

    async def lock_time_off(
        self, psychologist_id: uuid.UUID, time_off_id: uuid.UUID
    ) -> TimeOff | None:
        return await self._session.scalar(
            select(TimeOff)
            .where(TimeOff.id == time_off_id, TimeOff.psychologist_id == psychologist_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def delete_time_off(self, psychologist_id: uuid.UUID, time_off_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(TimeOff)
            .where(TimeOff.id == time_off_id, TimeOff.psychologist_id == psychologist_id)
            .returning(TimeOff.id)
        )
        return result.scalar_one_or_none() is not None
