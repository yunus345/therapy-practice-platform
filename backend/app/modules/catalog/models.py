import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CHAR, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin, pg_enum


class DeliveryMode(StrEnum):
    IN_PERSON = "in_person"
    ONLINE = "online"


class Service(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "services"

    name: Mapped[str]
    description: Mapped[str | None]
    duration_minutes: Mapped[int]
    price_amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'TRY'"))
    delivery_mode: Mapped[DeliveryMode] = mapped_column(pg_enum(DeliveryMode, "delivery_mode"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))


class PsychologistService(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "psychologist_services"
    __table_args__ = (
        UniqueConstraint("psychologist_id", "service_id"),
        Index("ix_psychologist_services_service_id", "service_id"),
    )

    psychologist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("psychologist_profiles.user_id"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    custom_price_amount: Mapped[Decimal | None]
