"""Imports every model so that `Base.metadata` is complete for Alembic and tests."""

from app.core.models import Base
from app.modules.audit import models as audit_models
from app.modules.auth import models as auth_models
from app.modules.catalog import models as catalog_models
from app.modules.clients import models as clients_models
from app.modules.consent import models as consent_models
from app.modules.scheduling import models as scheduling_models
from app.modules.users import models as users_models

__all__ = [
    "Base",
    "audit_models",
    "auth_models",
    "catalog_models",
    "clients_models",
    "consent_models",
    "scheduling_models",
    "users_models",
]
