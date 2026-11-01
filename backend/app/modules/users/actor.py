"""The authenticated caller, passed explicitly to every service that authorizes."""

import uuid
from dataclasses import dataclass

from app.core.errors import AppError
from app.modules.users.models import UserRole


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have access to this resource."


STAFF_ROLES = frozenset({UserRole.ADMIN, UserRole.PSYCHOLOGIST})


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    role: UserRole

    def require_role(self, *roles: UserRole) -> None:
        if self.role not in roles:
            raise ForbiddenError
