"""User records and profiles. Access control lies with the callers."""

import uuid
from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import AppError, NotFoundError
from app.modules.users.models import PsychologistProfile, User
from app.modules.users.repository import UserRepository


class EmailTakenError(AppError):
    status_code = 409
    code = "EMAIL_TAKEN"
    message = "This email is already in use."


class SlugTakenError(AppError):
    status_code = 409
    code = "SLUG_TAKEN"
    message = "This profile address is already in use."


class UserService:
    def __init__(self, repository: UserRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._repository.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self._repository.get_by_email(email)

    async def create(self, user: User) -> None:
        if not await self._repository.add_unique(user):
            raise EmailTakenError

    async def create_profile(self, profile: PsychologistProfile) -> None:
        if not await self._repository.add_unique(profile):
            raise SlugTakenError

    async def update_profile(
        self, profile: PsychologistProfile, changes: Mapping[str, Any]
    ) -> None:
        if not await self._repository.update_profile(profile, changes):
            raise SlugTakenError

    async def get_profile(self, user_id: uuid.UUID) -> PsychologistProfile | None:
        return await self._repository.get_profile(user_id)

    async def get_active_profile_by_slug(self, slug: str) -> PsychologistProfile | None:
        """Return the profile of a psychologist whose account can receive bookings."""
        return await self._repository.get_active_profile_by_slug(slug)

    async def get_profiles(
        self, user_ids: Collection[uuid.UUID]
    ) -> Mapping[uuid.UUID, PsychologistProfile]:
        profiles = await self._repository.get_profiles(user_ids)
        return {profile.user_id: profile for profile in profiles}

    async def lock_profile(self, user_id: uuid.UUID, exclusive: bool) -> PsychologistProfile:
        """Lock a profile until the transaction ends and return its current state.

        Shared locks let staff bookings run in parallel, leaving the exclusion constraint to
        decide between them. An exclusive lock is for checks that constraint cannot make,
        such as buffers and time off.
        """
        profile = await self._repository.lock_profile(user_id, exclusive)
        if profile is None:
            raise NotFoundError
        return profile

    async def record_failed_attempt(
        self, user: User, max_attempts: int, lockout: timedelta
    ) -> None:
        await self._repository.record_failed_attempt(user.id, max_attempts, lockout, self._clock())

    async def record_successful_login(self, user: User, now: datetime) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        await self.save()

    async def claim_totp_step(self, user: User, step: int) -> bool:
        return await self._repository.claim_totp_step(user.id, step)

    async def save(self) -> None:
        await self._repository.flush()


def build_user_service(session: AsyncSession, clock: Clock) -> UserService:
    return UserService(UserRepository(session), clock)
