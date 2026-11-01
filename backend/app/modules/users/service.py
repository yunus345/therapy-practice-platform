"""User records. Access control lies with the callers: auth and the admin service."""

import uuid
from datetime import datetime, timedelta

from app.core.clock import Clock
from app.core.errors import AppError
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
