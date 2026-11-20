"""Writes the demo data into a database that holds no users yet."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.users.models import User
from app.seed.generator import DemoData, build_demo_data

# Every demo account shares this password. The seed refuses to run outside dev.
DEMO_PASSWORD = "seans-demo-2026"  # noqa: S105


class DatabaseNotEmptyError(Exception):
    """The seed only fills an empty database, so it can never mix with real data."""


async def load_demo_data(session: AsyncSession, today: date) -> DemoData:
    if await session.scalar(select(User.id).limit(1)) is not None:
        raise DatabaseNotEmptyError
    # One hash for every account: they share the password anyway, and argon2 is slow.
    data = build_demo_data(today, await hash_password(DEMO_PASSWORD))
    for rows in data.groups():
        session.add_all(rows)
        await session.flush()
    return data
