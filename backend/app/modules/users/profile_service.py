"""A psychologist's own profile. Every method requires the psychologist role."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import NotFoundError
from app.core.models import changed_values
from app.modules.audit.service import AuditAction, AuditService, build_audit_service
from app.modules.users.actor import Actor
from app.modules.users.models import PsychologistProfile, UserRole
from app.modules.users.schemas import UpdateProfileRequest
from app.modules.users.service import UserService, build_user_service

PROFILE_ENTITY = "psychologist_profile"


class ProfileService:
    def __init__(self, users: UserService, audit: AuditService) -> None:
        self._users = users
        self._audit = audit

    async def get_own(self, actor: Actor) -> PsychologistProfile:
        actor.require_role(UserRole.PSYCHOLOGIST)
        profile = await self._users.get_profile(actor.user_id)
        if profile is None:
            raise NotFoundError
        return profile

    async def update_own(
        self, actor: Actor, request: UpdateProfileRequest, *, ip_address: str | None
    ) -> PsychologistProfile:
        profile = await self.get_own(actor)
        changes = changed_values(profile, request.model_dump(exclude_unset=True))
        if changes:
            await self._users.update_profile(profile, changes)
            await self._audit.record(
                AuditAction.PROFILE_UPDATED,
                actor_user_id=actor.user_id,
                entity_type=PROFILE_ENTITY,
                entity_id=actor.user_id,
                ip_address=ip_address,
                metadata={"changed_fields": sorted(changes)},
            )
        return profile


def build_profile_service(session: AsyncSession, clock: Clock) -> ProfileService:
    return ProfileService(build_user_service(session, clock), build_audit_service(session))
