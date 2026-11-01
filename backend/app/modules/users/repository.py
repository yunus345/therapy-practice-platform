import uuid
from datetime import datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import PsychologistProfile, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id, populate_existing=True)

    async def get_by_email(self, email: str) -> User | None:
        return await self._session.scalar(select(User).where(User.email == email))

    async def add_unique(self, row: User | PsychologistProfile) -> bool:
        """Insert a row, returning False if it violates a unique constraint."""
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def slug_exists(self, slug: str) -> bool:
        statement = select(PsychologistProfile.user_id).where(PsychologistProfile.slug == slug)
        return await self._session.scalar(statement) is not None

    async def flush(self) -> None:
        await self._session.flush()

    async def record_failed_attempt(
        self, user_id: uuid.UUID, max_attempts: int, lockout: timedelta, now: datetime
    ) -> None:
        """Count a failure atomically, so that parallel guesses cannot skip the lockout."""
        attempts = User.failed_login_attempts + 1
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_attempts=case((attempts >= max_attempts, 0), else_=attempts),
                locked_until=case(
                    (attempts >= max_attempts, now + lockout), else_=User.locked_until
                ),
            )
        )

    async def claim_totp_step(self, user_id: uuid.UUID, step: int) -> bool:
        """Accept a time step only once, even for parallel requests."""
        used_step = func.coalesce(User.totp_last_used_step, -1)
        result = await self._session.execute(
            update(User)
            .where(User.id == user_id, used_step < step)
            .values(totp_last_used_step=step)
            .returning(User.id)
        )
        return result.scalar_one_or_none() is not None
