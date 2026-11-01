"""Administrative actions on accounts. Every method requires the admin role."""

import uuid

from app.core.clock import Clock
from app.core.errors import AppError
from app.core.security import hash_password
from app.modules.audit.service import AuditAction, AuditService
from app.modules.auth.passwords import ensure_acceptable_password
from app.modules.auth.service import AuthService
from app.modules.users.actor import Actor
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.modules.users.service import UserService

USER_ENTITY = "user"


class UserNotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Not Found"


class CannotTargetSelfError(AppError):
    status_code = 409
    code = "CANNOT_TARGET_SELF"
    message = "This action cannot be applied to your own account."


class AdminService:
    def __init__(
        self, users: UserService, auth: AuthService, audit: AuditService, clock: Clock
    ) -> None:
        self._users = users
        self._auth = auth
        self._audit = audit
        self._clock = clock

    async def create_psychologist(
        self,
        actor: Actor,
        *,
        email: str,
        first_name: str,
        last_name: str,
        slug: str,
        title: str | None,
        ip_address: str | None,
    ) -> User:
        actor.require_role(UserRole.ADMIN)
        user = User(email=email, role=UserRole.PSYCHOLOGIST)
        await self._users.create(user)
        await self._users.create_profile(
            PsychologistProfile(
                user_id=user.id,
                first_name=first_name,
                last_name=last_name,
                slug=slug,
                title=title,
            )
        )
        await self._auth.invite(user)
        await self._record(AuditAction.USER_CREATED, actor, user.id, ip_address)
        return user

    async def set_active(
        self, actor: Actor, user_id: uuid.UUID, *, active: bool, ip_address: str | None
    ) -> User:
        actor.require_role(UserRole.ADMIN)
        user = await self._get_other_user(actor, user_id)
        user.is_active = active
        await self._users.save()
        if not active:
            await self._auth.revoke_all_sessions(user.id)
        action = AuditAction.USER_ACTIVATED if active else AuditAction.USER_DEACTIVATED
        await self._record(action, actor, user.id, ip_address)
        return user

    async def reset_two_factor(
        self, actor: Actor, user_id: uuid.UUID, *, ip_address: str | None
    ) -> None:
        """Clear 2FA for a staff member who lost their device and recovery codes."""
        actor.require_role(UserRole.ADMIN)
        user = await self._get_other_user(actor, user_id)
        await self._auth.clear_two_factor(user)
        await self._auth.revoke_all_sessions(user.id)
        await self._record(AuditAction.TWO_FACTOR_RESET, actor, user.id, ip_address)

    async def _get_other_user(self, actor: Actor, user_id: uuid.UUID) -> User:
        if user_id == actor.user_id:
            raise CannotTargetSelfError
        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFoundError
        return user

    async def _record(
        self, action: AuditAction, actor: Actor, user_id: uuid.UUID, ip_address: str | None
    ) -> None:
        await self._audit.record(
            action,
            actor_user_id=actor.user_id,
            entity_type=USER_ENTITY,
            entity_id=user_id,
            ip_address=ip_address,
        )


async def create_first_admin(
    users: UserService, audit: AuditService, clock: Clock, email: str, password: str
) -> User:
    """Create an admin from the command line, where no admin session exists yet."""
    ensure_acceptable_password(password, email)
    user = User(
        email=email,
        role=UserRole.ADMIN,
        password_hash=await hash_password(password),
        email_verified_at=clock(),
    )
    await users.create(user)
    await audit.record(
        AuditAction.USER_CREATED, actor_user_id=None, entity_type=USER_ENTITY, entity_id=user.id
    )
    return user
