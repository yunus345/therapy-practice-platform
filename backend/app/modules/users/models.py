import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin, pg_enum


class UserRole(StrEnum):
    ADMIN = "admin"
    PSYCHOLOGIST = "psychologist"
    CLIENT = "client"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    # Null until an invited psychologist sets a password.
    password_hash: Mapped[str | None]
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    email_verified_at: Mapped[datetime | None]
    totp_secret_encrypted: Mapped[str | None]
    totp_enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    # Last accepted TOTP time step, so that a code cannot be used twice.
    totp_last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    failed_login_attempts: Mapped[int] = mapped_column(server_default=text("0"))
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]


class PsychologistProfile(TimestampMixin, Base):
    __tablename__ = "psychologist_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    first_name: Mapped[str]
    last_name: Mapped[str]
    title: Mapped[str | None]
    slug: Mapped[str] = mapped_column(unique=True)
    bio: Mapped[str | None]
    phone: Mapped[str | None]
    timezone: Mapped[str] = mapped_column(server_default=text("'Europe/Istanbul'"))
    min_booking_notice_hours: Mapped[int] = mapped_column(server_default=text("24"))
    max_booking_advance_days: Mapped[int] = mapped_column(server_default=text("60"))
    cancellation_window_hours: Mapped[int] = mapped_column(server_default=text("24"))
    auto_confirm_bookings: Mapped[bool] = mapped_column(server_default=text("false"))
    buffer_minutes: Mapped[int] = mapped_column(server_default=text("0"))
