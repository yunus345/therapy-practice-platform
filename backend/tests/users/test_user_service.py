import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, pool, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.modules.users.service import UserService, build_user_service
from tests.conftest import UNKNOWN_ID, UserFactory
from tests.conftest import test_database_url as database_url


@pytest.fixture
def users(db_session: AsyncSession) -> UserService:
    return build_user_service(db_session, utcnow)


async def get_slug(session: AsyncSession, user: User) -> str:
    profile = await session.get(PsychologistProfile, user.id)
    assert profile is not None
    return profile.slug


async def test_get_profile_returns_the_profile(users: UserService, make_user: UserFactory) -> None:
    user = await make_user(UserRole.PSYCHOLOGIST)

    profile = await users.get_profile(user.id)

    assert profile is not None
    assert profile.user_id == user.id


async def test_get_profile_of_unknown_user_is_none(users: UserService) -> None:
    assert await users.get_profile(UNKNOWN_ID) is None


async def test_active_psychologist_is_found_by_slug(
    users: UserService, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user(UserRole.PSYCHOLOGIST)

    profile = await users.get_active_profile_by_slug(await get_slug(db_session, user))

    assert profile is not None
    assert profile.user_id == user.id


async def test_deactivated_psychologist_is_not_found_by_slug(
    users: UserService, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user(UserRole.PSYCHOLOGIST, is_active=False)

    assert await users.get_active_profile_by_slug(await get_slug(db_session, user)) is None


async def test_profile_of_another_role_is_not_found_by_slug(
    users: UserService, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user(UserRole.CLIENT)
    db_session.add(PsychologistProfile(user_id=user.id, first_name="A", last_name="B", slug="odd"))
    await db_session.commit()

    assert await users.get_active_profile_by_slug("odd") is None


async def test_unknown_slug_is_not_found(users: UserService) -> None:
    assert await users.get_active_profile_by_slug("nobody") is None


async def test_get_profiles_maps_ids_and_includes_deactivated_accounts(
    users: UserService, make_user: UserFactory
) -> None:
    active = await make_user(UserRole.PSYCHOLOGIST)
    deactivated = await make_user(UserRole.PSYCHOLOGIST, is_active=False)

    profiles = await users.get_profiles([active.id, deactivated.id, UNKNOWN_ID])

    assert set(profiles) == {active.id, deactivated.id}
    assert profiles[active.id].user_id == active.id


async def test_get_profiles_of_no_ids_is_empty(users: UserService) -> None:
    assert await users.get_profiles([]) == {}


async def test_locking_an_unknown_profile_is_not_found(users: UserService) -> None:
    with pytest.raises(NotFoundError):
        await users.lock_profile(UNKNOWN_ID, exclusive=False)


class TestLockProfile:
    """Row locks need a committed row and separate connections, so these tests commit."""

    @pytest.fixture
    async def engine(self, migrated_database: None) -> AsyncIterator[AsyncEngine]:
        engine = create_async_engine(database_url(), poolclass=pool.NullPool)
        yield engine
        await engine.dispose()

    @pytest.fixture
    async def user_id(self, engine: AsyncEngine) -> AsyncIterator[uuid.UUID]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = User(email="locked@example.com", role=UserRole.PSYCHOLOGIST)
            session.add(user)
            await session.flush()
            session.add(
                PsychologistProfile(
                    user_id=user.id, first_name="A", last_name="B", slug="locked-profile"
                )
            )
            await session.commit()
        yield user.id
        async with AsyncSession(engine) as session:
            await session.execute(
                delete(PsychologistProfile).where(PsychologistProfile.user_id == user.id)
            )
            await session.execute(delete(User).where(User.id == user.id))
            await session.commit()

    @staticmethod
    async def can_lock(engine: AsyncEngine, user_id: uuid.UUID, *, shared: bool) -> bool:
        """Try a lock from another connection without waiting for it."""
        statement = (
            select(PsychologistProfile.user_id)
            .where(PsychologistProfile.user_id == user_id)
            .with_for_update(read=shared, nowait=True)
        )
        async with AsyncSession(engine) as session:
            try:
                await session.execute(statement)
            except DBAPIError as error:
                assert getattr(error.orig, "sqlstate", None) == "55P03"
                return False
            return True

    async def test_shared_lock_admits_bookings_and_stops_exclusive_work(
        self, engine: AsyncEngine, user_id: uuid.UUID
    ) -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await build_user_service(session, utcnow).lock_profile(user_id, exclusive=False)

            assert await self.can_lock(engine, user_id, shared=True) is True
            assert await self.can_lock(engine, user_id, shared=False) is False

        assert await self.can_lock(engine, user_id, shared=False) is True

    async def test_exclusive_lock_stops_shared_locks(
        self, engine: AsyncEngine, user_id: uuid.UUID
    ) -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await build_user_service(session, utcnow).lock_profile(user_id, exclusive=True)

            assert await self.can_lock(engine, user_id, shared=True) is False

        assert await self.can_lock(engine, user_id, shared=True) is True

    async def test_lock_returns_the_current_state_of_a_loaded_profile(
        self, engine: AsyncEngine, user_id: uuid.UUID
    ) -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            users = build_user_service(session, utcnow)
            loaded = await users.get_profile(user_id)
            assert loaded is not None
            assert loaded.buffer_minutes == 0
            await session.commit()

            async with AsyncSession(engine) as other:
                await other.execute(
                    update(PsychologistProfile)
                    .where(PsychologistProfile.user_id == user_id)
                    .values(buffer_minutes=30)
                )
                await other.commit()

            locked = await users.lock_profile(user_id, exclusive=False)

            assert locked.buffer_minutes == 30
