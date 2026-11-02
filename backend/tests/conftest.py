import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import make_url, pool, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import REPO_ROOT, Environment, Settings, get_settings
from app.core.security import hash_password
from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditAction
from app.modules.auth.csrf import CSRF_COOKIE
from app.modules.catalog.models import DeliveryMode, PsychologistService, Service
from app.modules.clients.models import Client, PsychologistClient
from app.modules.notifications.service import Email
from app.modules.scheduling.models import Appointment, AppointmentStatus, BookingChannel
from app.modules.users.models import PsychologistProfile, User, UserRole

STRONG_SECRET = "x" * 32
UNKNOWN_ID = uuid.UUID(int=0)
# Long enough for a call that takes no lock to finish. A call that waits for a lock
# cannot finish at all, so a slow machine does not make these tests fail.
LOCK_WAIT_SECONDS = 0.2

SettingsFactory = Callable[..., Settings]


def test_database_url() -> str:
    url = get_settings().test_database_url
    assert url is not None, "TEST_DATABASE_URL must be set"
    return url.get_secret_value()


def alembic_config() -> Config:
    """Alembic config for the test database, connecting as the owner role."""
    owner_url = make_url(get_settings().migration_database_url.get_secret_value()).set(
        database=make_url(test_database_url()).database
    )
    config = Config(REPO_ROOT / "backend" / "alembic.ini")
    config.attributes["database_url"] = owner_url.render_as_string(hide_password=False)
    return config


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    """Rebuild the schema, so that leftover rows from earlier runs cannot affect tests."""
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture
async def db_connection(migrated_database: None) -> AsyncIterator[AsyncConnection]:
    """Connection on the restricted role. Everything done on it rolls back after the test."""
    engine = create_async_engine(test_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session = AsyncSession(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    yield session
    await session.close()


@pytest.fixture
async def committed_engine(migrated_database: None) -> AsyncIterator[AsyncEngine]:
    """Connections whose commits are real, for tests of locks between transactions.

    Every table is emptied afterwards. Only the owner role in replica mode can do that,
    because the audit log rejects any other way of removing its rows.
    """
    engine = create_async_engine(test_database_url(), poolclass=pool.NullPool)
    yield engine
    await engine.dispose()

    owner = create_async_engine(
        alembic_config().attributes["database_url"], poolclass=pool.NullPool
    )
    async with owner.begin() as connection:
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        tables = await connection.scalars(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        await connection.execute(text(f"TRUNCATE {', '.join(tables)}"))
    await owner.dispose()


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": Environment.DEV,
            "database_url": test_database_url(),
            "migration_database_url": test_database_url(),
            "csrf_secret": STRONG_SECRET,
            "totp_encryption_key": STRONG_SECRET,
        }
        return Settings.model_validate(values | overrides)

    return factory


@pytest.fixture
async def app(make_settings: SettingsFactory) -> AsyncIterator[FastAPI]:
    application = create_app(make_settings())
    yield application
    await application.state.database.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


DEFAULT_PASSWORD = "a-long-unique-test-passphrase"
CSRF_HEADER = "X-CSRF-Token"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    async def send(self, email: Email) -> None:
        self.sent.append(email)

    def last_token(self, to: str) -> str:
        email = next(email for email in reversed(self.sent) if email.to == to)
        assert email.link is not None
        match = re.search(r"#token=(.+)$", email.link)
        assert match is not None
        return match.group(1)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def api_settings() -> dict[str, Any]:
    """Overrides for the API under test. Tests may change this before using `api_app`."""
    return {}


@pytest.fixture
async def api_app(
    make_settings: SettingsFactory,
    api_settings: dict[str, Any],
    db_connection: AsyncConnection,
    clock: FakeClock,
    mailer: FakeMailer,
) -> AsyncIterator[FastAPI]:
    """The application with its sessions bound to the test transaction."""
    application = create_app(make_settings(**api_settings))
    application.state.database.sessions = async_sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    application.state.clock = clock
    application.state.mailer = mailer
    yield application
    await application.state.database.dispose()


ClientFactory = Callable[[], Awaitable[httpx.AsyncClient]]


@asynccontextmanager
async def browser_clients(application: FastAPI) -> AsyncIterator[ClientFactory]:
    """Create browser-like clients, each with its own cookies and a CSRF token."""
    clients: list[httpx.AsyncClient] = []

    async def factory() -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=application)
        client = httpx.AsyncClient(transport=transport, base_url="http://test/api/v1")

        async def add_csrf_header(request: httpx.Request) -> None:
            token = client.cookies.get(CSRF_COOKIE)
            if token and request.method != "GET" and CSRF_HEADER not in request.headers:
                request.headers[CSRF_HEADER] = token

        client.event_hooks["request"].append(add_csrf_header)
        clients.append(client)
        await client.get("/auth/csrf")
        return client

    try:
        yield factory
    finally:
        for client in clients:
            await client.aclose()


@pytest.fixture
async def new_client(api_app: FastAPI) -> AsyncIterator[ClientFactory]:
    async with browser_clients(api_app) as factory:
        yield factory


@pytest.fixture
async def api(new_client: ClientFactory) -> httpx.AsyncClient:
    return await new_client()


@pytest.fixture
async def committed_app(
    make_settings: SettingsFactory,
    api_settings: dict[str, Any],
    committed_engine: AsyncEngine,
    clock: FakeClock,
    mailer: FakeMailer,
) -> AsyncIterator[FastAPI]:
    """The application on a real `Database`, so that requests compete as separate transactions.

    Connections of `committed_engine` see what its requests commit, and its cleanup empties
    the tables afterwards.
    """
    application = create_app(make_settings(**api_settings))
    application.state.clock = clock
    application.state.mailer = mailer
    yield application
    await application.state.database.dispose()


@pytest.fixture
async def new_committed_client(committed_app: FastAPI) -> AsyncIterator[ClientFactory]:
    async with browser_clients(committed_app) as factory:
        yield factory


UserFactory = Callable[..., Awaitable[User]]


@pytest.fixture
def make_user(db_session: AsyncSession) -> UserFactory:
    """Insert a verified user with a password, and a profile for psychologists."""
    counter = iter(range(1, 10_000))

    async def factory(
        role: UserRole = UserRole.CLIENT, password: str = DEFAULT_PASSWORD, **fields: Any
    ) -> User:
        number = next(counter)
        values: dict[str, Any] = {
            "email": f"user{number}@example.com",
            "role": role,
            "password_hash": await hash_password(password),
            "email_verified_at": datetime.now(UTC),
        }
        user = User(**(values | fields))
        db_session.add(user)
        await db_session.flush()
        if role is UserRole.PSYCHOLOGIST:
            db_session.add(
                PsychologistProfile(
                    user_id=user.id, first_name="A", last_name="B", slug=f"psychologist-{number}"
                )
            )
            await db_session.flush()
        await db_session.commit()
        return user

    return factory


ServiceFactory = Callable[..., Awaitable[Service]]
ServiceAssigner = Callable[..., Awaitable[None]]


@pytest.fixture
def make_service(db_session: AsyncSession) -> ServiceFactory:
    """Insert an active service. Keyword arguments replace the defaults."""
    counter = iter(range(1, 10_000))

    async def factory(**fields: Any) -> Service:
        values: dict[str, Any] = {
            "name": f"Service {next(counter):04}",
            "duration_minutes": 50,
            "price_amount": Decimal("1500.00"),
            "delivery_mode": DeliveryMode.IN_PERSON,
        }
        service = Service(**(values | fields))
        db_session.add(service)
        await db_session.commit()
        return service

    return factory


@pytest.fixture
def assign_service(db_session: AsyncSession) -> ServiceAssigner:
    async def assign(
        psychologist: User, service: Service, custom_price_amount: Decimal | None = None
    ) -> None:
        db_session.add(
            PsychologistService(
                psychologist_id=psychologist.id,
                service_id=service.id,
                custom_price_amount=custom_price_amount,
            )
        )
        await db_session.commit()

    return assign


ClientRecordFactory = Callable[..., Awaitable[Client]]


@pytest.fixture
def make_client_record(db_session: AsyncSession) -> ClientRecordFactory:
    """Insert a client record in the active care of the given psychologists."""
    counter = iter(range(1, 10_000))

    async def factory(psychologist: User, *colleagues: User, **fields: Any) -> Client:
        values: dict[str, Any] = {
            "first_name": "Ada",
            "last_name": f"Client {next(counter):04}",
            "phone": "+90 532 000 00 00",
            "created_by_user_id": psychologist.id,
        }
        client = Client(**(values | fields))
        db_session.add(client)
        await db_session.flush()
        db_session.add_all(
            PsychologistClient(psychologist_id=user.id, client_id=client.id)
            for user in (psychologist, *colleagues)
        )
        await db_session.commit()
        return client

    return factory


AppointmentFactory = Callable[..., Awaitable[Appointment]]


@pytest.fixture
def make_appointment(
    db_session: AsyncSession, make_service: ServiceFactory, clock: FakeClock
) -> AppointmentFactory:
    """Insert an appointment directly, without the booking rules. By default it is tomorrow."""

    async def factory(psychologist: User, client: Client, **fields: Any) -> Appointment:
        service = await make_service()
        starts_at = fields.pop("starts_at", clock.now + timedelta(days=1))
        values: dict[str, Any] = {
            "psychologist_id": psychologist.id,
            "client_id": client.id,
            "service_id": service.id,
            "starts_at": starts_at,
            "ends_at": starts_at + timedelta(minutes=service.duration_minutes),
            "status": AppointmentStatus.CONFIRMED,
            "price_amount": service.price_amount,
            "currency": service.currency,
            "booking_channel": BookingChannel.STAFF,
            "booked_by_user_id": psychologist.id,
        }
        appointment = Appointment(**(values | fields))
        db_session.add(appointment)
        await db_session.commit()
        return appointment

    return factory


@pytest.fixture
async def admin(api: httpx.AsyncClient, make_user: UserFactory) -> User:
    user = await make_user(UserRole.ADMIN)
    await log_in(api, user.email)
    return user


@pytest.fixture
async def psychologist(api: httpx.AsyncClient, make_user: UserFactory) -> User:
    user = await make_user(UserRole.PSYCHOLOGIST)
    await log_in(api, user.email)
    return user


def replace_cookie(client: httpx.AsyncClient, name: str, value: str) -> None:
    client.cookies.delete(name)
    client.cookies.set(name, value)


async def log_in(
    client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> httpx.Response:
    return await client.post("/auth/login", json={"email": email, "password": password})


async def assert_blocked(call: asyncio.Task[Any]) -> None:
    done, _ = await asyncio.wait({call}, timeout=LOCK_WAIT_SECONDS)
    assert not done


async def audit_entries(session: AsyncSession, action: AuditAction) -> Sequence[AuditLog]:
    result = await session.scalars(select(AuditLog).where(AuditLog.action == action))
    return result.all()
