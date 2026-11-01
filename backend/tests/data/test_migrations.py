from sqlalchemy import Enum, text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.models import Base
from tests.conftest import alembic_config


def test_migrations_downgrade_and_reapply_from_scratch(migrated_database: None) -> None:
    config = alembic_config()

    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_models_match_migrations(migrated_database: None) -> None:
    command.check(alembic_config())


async def test_python_enums_match_database_enums(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text(
            "SELECT t.typname, array_agg(e.enumlabel::text ORDER BY e.enumsortorder) "
            "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid GROUP BY t.typname"
        )
    )
    database_enums = {row[0]: row[1] for row in rows.all()}

    enum_types = {
        column.type.name: column.type.enums
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Enum)
    }

    assert enum_types == database_enums


async def test_every_mutable_table_has_an_updated_at_trigger(db_session: AsyncSession) -> None:
    triggers = set(
        await db_session.scalars(
            text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'trg_%_updated_at'")
        )
    )
    expected = {
        f"trg_{table.name}_updated_at"
        for table in Base.metadata.tables.values()
        if "updated_at" in table.columns
    }

    assert triggers == expected
