"""Loading the demo data, and the guards that keep it away from real data."""

import sys
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment
from app.core.security import verify_password
from app.modules.scheduling.models import Appointment, AppointmentStatusHistory
from app.modules.users.models import User, UserRole
from app.seed import __main__ as cli
from app.seed.fixtures import ADMIN_EMAIL
from app.seed.generator import DemoData, build_demo_data
from app.seed.loader import DEMO_PASSWORD, DatabaseNotEmptyError, load_demo_data
from tests.conftest import SettingsFactory, UserFactory

TODAY = date(2026, 11, 20)


async def count(session: AsyncSession, model: type[Any]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_loads_into_an_empty_database(db_session: AsyncSession) -> None:
    data = await load_demo_data(db_session, TODAY)

    assert await count(db_session, User) == len(data.users)
    assert await count(db_session, Appointment) == len(data.appointments)
    assert await count(db_session, AppointmentStatusHistory) == len(data.history)


async def test_demo_accounts_log_in_with_the_demo_password(db_session: AsyncSession) -> None:
    await load_demo_data(db_session, TODAY)

    admin = await db_session.scalar(select(User).where(User.email == ADMIN_EMAIL))

    assert admin is not None
    assert admin.role is UserRole.ADMIN
    assert await verify_password(admin.password_hash, DEMO_PASSWORD)


async def test_refuses_a_database_that_has_users(
    db_session: AsyncSession, make_user: UserFactory
) -> None:
    await make_user(UserRole.ADMIN)

    with pytest.raises(DatabaseNotEmptyError):
        await load_demo_data(db_session, TODAY)

    assert await count(db_session, User) == 1


@pytest.mark.parametrize("environment", [Environment.TEST, Environment.PRODUCTION])
def test_the_command_runs_only_in_dev(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: SettingsFactory,
    environment: Environment,
) -> None:
    settings = make_settings().model_copy(update={"environment": environment})
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["python -m app.seed"])

    async def must_not_run(today: date) -> None:
        raise AssertionError

    monkeypatch.setattr(cli, "seed", must_not_run)

    with pytest.raises(SystemExit, match="ENVIRONMENT=dev"):
        cli.main()


async def test_the_report_lists_the_demo_logins(db_session: AsyncSession) -> None:
    data = await load_demo_data(db_session, TODAY)

    report = cli.report(data)

    assert DEMO_PASSWORD in report
    assert ADMIN_EMAIL in report
    assert f"{len(data.appointments)} appointments" in report


def test_the_command_prints_the_report_in_dev(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: SettingsFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = build_demo_data(TODAY, "hash")
    monkeypatch.setattr(cli, "get_settings", make_settings)
    monkeypatch.setattr(sys, "argv", ["python -m app.seed", "--today", TODAY.isoformat()])
    days: list[date] = []

    async def fake_seed(today: date) -> DemoData:
        days.append(today)
        return data

    monkeypatch.setattr(cli, "seed", fake_seed)

    cli.main()

    assert days == [TODAY]
    assert capsys.readouterr().out == cli.report(data)


def test_the_command_explains_a_refusal(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    monkeypatch.setattr(cli, "get_settings", make_settings)
    monkeypatch.setattr(sys, "argv", ["python -m app.seed"])

    async def full_database(today: date) -> DemoData:
        raise DatabaseNotEmptyError

    monkeypatch.setattr(cli, "seed", full_database)

    with pytest.raises(SystemExit, match="already has users"):
        cli.main()
