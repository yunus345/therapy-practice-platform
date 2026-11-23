from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

REQUIRED_EXTENSIONS = {"btree_gist", "citext", "pgcrypto", "vector"}


async def test_application_role_is_restricted_and_extensions_are_available() -> None:
    url = get_settings().test_database_url
    assert url is not None, "TEST_DATABASE_URL must be set"

    engine = create_async_engine(url.get_secret_value())
    async with engine.connect() as connection:
        version = await connection.scalar(text("SHOW server_version_num"))
        is_superuser = await connection.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        )
        available = await connection.scalars(text("SELECT name FROM pg_available_extensions"))
        extensions = set(available)
    await engine.dispose()

    assert int(version) // 10000 == 17
    assert is_superuser is False
    assert REQUIRED_EXTENSIONS <= extensions
