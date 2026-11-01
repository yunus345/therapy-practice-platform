import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CHAR, ForeignKey, Index, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

NO_OVERLAP_CONSTRAINT = "ex_appointments_no_overlap"


class AppointmentStatus(StrEnum):
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class BookingChannel(StrEnum):
    ONLINE = "online"
    STAFF = "staff"


class CancellationReason(StrEnum):
    CLIENT_REQUEST = "client_request"
    PSYCHOLOGIST_REQUEST = "psychologist_request"
    LATE_CANCELLATION = "late_cancellation"
    RESCHEDULED = "rescheduled"
    OTHER = "other"


class AvailabilityRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Weekly working hours in the psychologist's local time. Weekday 0 is Monday."""

    __tablename__ = "availability_rules"
    __table_args__ = (
        Index("ix_availability_rules_psychologist_id_weekday", "psychologist_id", "weekday"),
    )

    psychologist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("psychologist_profiles.user_id"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time]
    end_time: Mapped[time]
    valid_from: Mapped[date]
    valid_until: Mapped[date | None]


class TimeOff(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "time_off"
    __table_args__ = (
        Index("ix_time_off_psychologist_id_starts_at", "psychologist_id", "starts_at"),
    )

    psychologist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("psychologist_profiles.user_id"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    # Internal only, never exposed publicly.
    reason: Mapped[str | None] = mapped_column(String(200))


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_psychologist_id_starts_at", "psychologist_id", "starts_at"),
        Index("ix_appointments_client_id_starts_at", "client_id", "starts_at"),
    )

    psychologist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("psychologist_profiles.user_id"))
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    status: Mapped[AppointmentStatus] = mapped_column(
        pg_enum(AppointmentStatus, "appointment_status")
    )
    price_amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(CHAR(3))
    booking_channel: Mapped[BookingChannel] = mapped_column(
        pg_enum(BookingChannel, "booking_channel")
    )
    booked_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    rescheduled_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointments.id"), unique=True
    )
    cancelled_at: Mapped[datetime | None]
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    cancellation_reason: Mapped[CancellationReason | None] = mapped_column(
        pg_enum(CancellationReason, "cancellation_reason")
    )


class AppointmentStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only: the application role holds no UPDATE or DELETE privilege here."""

    __tablename__ = "appointment_status_history"
    __table_args__ = (
        Index(
            "ix_appointment_status_history_appointment_id_changed_at",
            "appointment_id",
            "changed_at",
        ),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointments.id"))
    from_status: Mapped[AppointmentStatus | None] = mapped_column(
        pg_enum(AppointmentStatus, "appointment_status")
    )
    to_status: Mapped[AppointmentStatus] = mapped_column(
        pg_enum(AppointmentStatus, "appointment_status")
    )
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # A category instead of free text, which could end up holding health information.
    reason_category: Mapped[CancellationReason | None] = mapped_column(
        pg_enum(CancellationReason, "cancellation_reason")
    )
