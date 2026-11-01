import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person receiving care, separate from a login account.

    Staff also register clients who book by phone, so `user_id` is optional. `email` is
    not unique: records are never matched across psychologists by email.
    """

    __tablename__ = "clients"
    __table_args__ = (
        Index("ix_clients_email", "email"),
        Index("ix_clients_last_name_first_name", "last_name", "first_name"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), unique=True)
    first_name: Mapped[str]
    last_name: Mapped[str]
    email: Mapped[str | None] = mapped_column(CITEXT)
    phone: Mapped[str]
    birth_date: Mapped[date | None]
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None]


class PsychologistClient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Care relationship used for object-level authorization."""

    __tablename__ = "psychologist_clients"
    __table_args__ = (
        UniqueConstraint("psychologist_id", "client_id"),
        Index("ix_psychologist_clients_client_id", "client_id"),
    )

    psychologist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("psychologist_profiles.user_id"))
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"))
    ended_at: Mapped[datetime | None]
