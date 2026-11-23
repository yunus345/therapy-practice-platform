import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.modules.catalog.models import Service
from app.modules.clients.models import Client
from app.modules.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    AvailabilityRule,
    CancellationReason,
    TimeOff,
)
from app.modules.users.models import PsychologistProfile, User, UserRole
from tests.conftest import (
    ClientFactory,
    FakeClock,
    ServiceAssigner,
    ServiceFactory,
    UserFactory,
    log_in,
)

RuleFactory = Callable[..., Awaitable[AvailabilityRule]]
TimeOffFactory = Callable[..., Awaitable[TimeOff]]

PERIOD_START = datetime(2026, 12, 8, 10, 0, tzinfo=UTC)
PERIOD_END = datetime(2026, 12, 8, 11, 0, tzinfo=UTC)

# A Monday, 09:00 in Istanbul. By default the first bookable moment is a day later.
NOW = datetime(2026, 11, 30, 6, 0, tzinfo=UTC)
TUESDAY = date(2026, 12, 8)
ISTANBUL_OFFSET = timedelta(hours=3)


def local(hour: int, minute: int = 0, day: date = TUESDAY) -> datetime:
    """A moment on the Istanbul clock, in UTC."""
    return datetime.combine(day, time(hour, minute), tzinfo=UTC) - ISTANBUL_OFFSET


SESSION_START = local(10)


@pytest.fixture
def fixed_time(clock: FakeClock) -> None:
    clock.now = NOW


@pytest.fixture
async def therapist(make_user: UserFactory) -> User:
    """A psychologist who is not logged in, whose schedule the tests act on."""
    return await make_user(UserRole.PSYCHOLOGIST)


@pytest.fixture
async def therapist_api(new_client: ClientFactory, therapist: User) -> httpx.AsyncClient:
    client = await new_client()
    await log_in(client, therapist.email)
    return client


@pytest.fixture
async def profile(therapist: User, db_session: AsyncSession) -> PsychologistProfile:
    return await load_profile(db_session, therapist)


@pytest.fixture
async def service(
    make_service: ServiceFactory, assign_service: ServiceAssigner, therapist: User
) -> Service:
    """A 50 minute session, offered by the psychologist."""
    service = await make_service(name="Individual Therapy", duration_minutes=50)
    await assign_service(therapist, service)
    return service


@pytest.fixture
def make_rule(db_session: AsyncSession) -> RuleFactory:
    """Insert a rule directly. By default it covers every Tuesday, 09:00 to 17:00."""

    async def factory(psychologist: User, **fields: Any) -> AvailabilityRule:
        values: dict[str, Any] = {
            "psychologist_id": psychologist.id,
            "weekday": 1,
            "start_time": time(9),
            "end_time": time(17),
            "valid_from": date(2020, 1, 1),
        }
        rule = AvailabilityRule(**(values | fields))
        db_session.add(rule)
        await db_session.commit()
        return rule

    return factory


@pytest.fixture
def make_time_off(db_session: AsyncSession) -> TimeOffFactory:
    async def factory(psychologist: User, **fields: Any) -> TimeOff:
        values: dict[str, Any] = {
            "psychologist_id": psychologist.id,
            "starts_at": PERIOD_START,
            "ends_at": PERIOD_END,
        }
        period = TimeOff(**(values | fields))
        db_session.add(period)
        await db_session.commit()
        return period

    return factory


@pytest.fixture
async def working_hours(therapist: User, make_rule: RuleFactory) -> AvailabilityRule:
    return await make_rule(therapist)


@dataclass(frozen=True)
class Booker:
    """A client with a login account, logged in through `api`."""

    user: User
    record: Client
    api: httpx.AsyncClient


BookerFactory = Callable[..., Awaitable[Booker]]


@pytest.fixture
def make_booker(
    db_session: AsyncSession, make_user: UserFactory, new_client: ClientFactory
) -> BookerFactory:
    """Insert a client with an account and no care relationship yet, and log them in."""

    async def factory(**user_fields: Any) -> Booker:
        user = await make_user(UserRole.CLIENT, **user_fields)
        record = Client(
            user_id=user.id,
            first_name="Zeynep",
            last_name="Yilmaz",
            email=user.email,
            phone="+90 532 987 65 43",
            created_by_user_id=user.id,
        )
        db_session.add(record)
        await db_session.commit()
        client = await new_client()
        await log_in(client, user.email)
        return Booker(user, record, client)

    return factory


@pytest.fixture
async def booker(make_booker: BookerFactory) -> Booker:
    return await make_booker()


@pytest.fixture
def statements(db_connection: AsyncConnection) -> Iterator[list[str]]:
    """Collect the SQL statements sent while a test runs."""
    seen: list[str] = []

    def remember(_: Any, __: Any, statement: str, *___: Any) -> None:
        seen.append(statement)

    engine = db_connection.sync_engine
    event.listen(engine, "before_cursor_execute", remember)
    yield seen
    event.remove(engine, "before_cursor_execute", remember)


def booking_body(
    profile: PsychologistProfile, service: Service, starts_at: datetime
) -> dict[str, str]:
    return {
        "psychologist_slug": profile.slug,
        "service_id": str(service.id),
        "starts_at": starts_at.isoformat(),
    }


async def book(
    booker: Booker,
    profile: PsychologistProfile,
    service: Service,
    starts_at: datetime = SESSION_START,
) -> httpx.Response:
    return await booker.api.post("/appointments", json=booking_body(profile, service, starts_at))


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


def listed_ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200
    return [item["id"] for item in response.json()["items"]]


def ids_of(*appointments: Appointment) -> list[str]:
    return [str(appointment.id) for appointment in appointments]


def in_status(status: AppointmentStatus) -> dict[str, Any]:
    """Columns of an appointment in a status, as the database checks require them."""
    if status is AppointmentStatus.CANCELLED:
        return {
            "status": status,
            "cancelled_at": NOW,
            "cancellation_reason": CancellationReason.OTHER,
        }
    return {"status": status}


async def load_appointment(session: AsyncSession, appointment_id: uuid.UUID) -> Appointment:
    appointment = await session.get(Appointment, appointment_id, populate_existing=True)
    assert appointment is not None
    return appointment


async def history_of(
    session: AsyncSession, appointment_id: uuid.UUID
) -> Sequence[AppointmentStatusHistory]:
    result = await session.scalars(
        select(AppointmentStatusHistory)
        .where(AppointmentStatusHistory.appointment_id == appointment_id)
        .order_by(AppointmentStatusHistory.from_status.is_not(None))
    )
    return result.all()


async def logged_in(
    new_client: ClientFactory, make_user: UserFactory, role: UserRole
) -> httpx.AsyncClient:
    client = await new_client()
    await log_in(client, (await make_user(role)).email)
    return client


async def load_profile(session: AsyncSession, user: User) -> PsychologistProfile:
    profile = await session.get(PsychologistProfile, user.id, populate_existing=True)
    assert profile is not None
    return profile
