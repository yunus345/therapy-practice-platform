import uuid
from enum import StrEnum
from typing import Any

from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository


class AuditAction(StrEnum):
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    EMAIL_VERIFIED = "auth.email_verified"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"  # noqa: S105
    PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"  # noqa: S105
    TWO_FACTOR_ENABLED = "auth.2fa_enabled"
    TWO_FACTOR_DISABLED = "auth.2fa_disabled"
    TWO_FACTOR_RESET = "auth.2fa_reset"
    TWO_FACTOR_FAILED = "auth.2fa_failed"
    TWO_FACTOR_VERIFIED = "auth.2fa_verified"
    RECOVERY_CODE_USED = "auth.recovery_code_used"
    SESSION_REVOKED = "auth.session_revoked"
    USER_REGISTERED = "users.registered"
    USER_CREATED = "users.created"
    USER_ACTIVATED = "users.activated"
    USER_DEACTIVATED = "users.deactivated"


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(
        self,
        action: AuditAction,
        *,
        actor_user_id: uuid.UUID | None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add an entry to the caller's transaction. Metadata must hold no personal data."""
        await self._repository.add(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                ip_address=ip_address,
                event_metadata=metadata or {},
            )
        )
