import pytest
from sqlalchemy import make_url, pool, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import alembic_config


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_logs SET action = 'changed'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ],
)
async def test_trigger_blocks_changes_even_for_the_owner(
    migrated_database: None, statement: str
) -> None:
    url = make_url(alembic_config().attributes["database_url"])
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("INSERT INTO audit_logs (action) VALUES ('test')"))
        with pytest.raises(DBAPIError, match="append-only"):
            await connection.execute(text(statement))
        await transaction.rollback()
    await engine.dispose()
