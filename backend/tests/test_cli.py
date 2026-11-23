from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.cli import create_admin
from app.core.database import Database
from app.modules.audit.service import AuditAction
from app.modules.users.models import User, UserRole
from tests.conftest import DEFAULT_PASSWORD, SettingsFactory, audit_entries
from tests.conftest import test_database_url as database_url


@pytest.fixture
def cli_database(
    db_connection: AsyncConnection,
    make_settings: SettingsFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the command at the test transaction, so that its commit rolls back later."""
    database = Database(database_url())
    database.sessions = async_sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    def build_database(_: Any) -> Database:
        return database

    monkeypatch.setattr("app.cli.Database", build_database)
    monkeypatch.setattr("app.cli.get_settings", make_settings)


async def test_create_admin_command_creates_a_verified_admin_and_audits_it(
    cli_database: None, db_session: AsyncSession
) -> None:
    await create_admin("root@example.com", DEFAULT_PASSWORD)

    user = await db_session.scalar(select(User).where(User.email == "root@example.com"))
    assert user is not None
    assert user.role is UserRole.ADMIN
    assert user.email_verified_at is not None
    [entry] = await audit_entries(db_session, AuditAction.USER_CREATED)
    assert entry.entity_id == user.id
