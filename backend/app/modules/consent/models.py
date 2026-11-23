import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin, pg_enum


class ConsentType(StrEnum):
    KVKK_DISCLOSURE = "kvkk_disclosure"
    DATA_PROCESSING = "data_processing"
    ANALYTICS = "analytics"
    AI_PROCESSING = "ai_processing"


class Consent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consents"
    __table_args__ = (
        Index(
            "uq_consents_active",
            "client_id",
            "consent_type",
            "version",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"))
    consent_type: Mapped[ConsentType] = mapped_column(pg_enum(ConsentType, "consent_type"))
    version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
    ip_address: Mapped[str | None] = mapped_column(INET)
    # Set when staff record a disclosure for a client who has no login account.
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
