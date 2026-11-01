"""Async database engine and request-scoped sessions."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str) -> None:
        # hide_parameters keeps bound values, which may be personal data, out of errors.
        self.engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()


def get_database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


async def get_read_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.sessions() as session:
        yield session


async def get_write_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.sessions() as session, session.begin():
        yield session


DatabaseDep = Annotated[Database, Depends(get_database)]
ReadSession = Annotated[AsyncSession, Depends(get_read_session)]
# scope="function" commits before the response is sent, so a client never sees a
# success for a transaction that failed to commit.
WriteSession = Annotated[AsyncSession, Depends(get_write_session, scope="function")]
