import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
    CLIENT_CREATED = "clients.created"
    CLIENT_UPDATED = "clients.updated"
    CLIENT_CARE_ENDED = "clients.care_ended"
    SERVICE_CREATED = "catalog.service_created"
    SERVICE_UPDATED = "catalog.service_updated"
    SERVICE_DEACTIVATED = "catalog.service_deactivated"
    SERVICE_ASSIGNED = "catalog.service_assigned"
    SERVICE_UNASSIGNED = "catalog.service_unassigned"
    PROFILE_UPDATED = "profile.updated"
    AVAILABILITY_CREATED = "availability.created"
    AVAILABILITY_UPDATED = "availability.updated"
    AVAILABILITY_DELETED = "availability.deleted"
    TIME_OFF_CREATED = "time_off.created"
    TIME_OFF_UPDATED = "time_off.updated"
    TIME_OFF_DELETED = "time_off.deleted"
    APPOINTMENT_CREATED = "appointments.created"
    APPOINTMENT_STATUS_CHANGED = "appointments.status_changed"
    APPOINTMENT_RESCHEDULED = "appointments.rescheduled"


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


def build_audit_service(session: AsyncSession) -> AuditService:
    return AuditService(AuditRepository(session))
