import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.core.schemas import PatchModel, ProfileSlug, UtcDatetime, bounded_text
from app.modules.catalog.models import DeliveryMode
from app.modules.catalog.schemas import OfferedServiceResponse
from app.modules.scheduling.models import AppointmentStatus, BookingChannel, CancellationReason


def _ensure_naive(value: time) -> time:
    if value.tzinfo is not None:
        raise PydanticCustomError("timezone_not_allowed", "This time cannot carry a timezone.")
    return value


Weekday = Annotated[int, Field(ge=0, le=6)]
# Working hours follow the local clock, so a fixed offset would drift across DST changes.
LocalTime = Annotated[time, AfterValidator(_ensure_naive)]
TimeOffReason = Annotated[str, bounded_text(200)]
# Pydantic would render a zero offset as Z, unlike every other offset.
OffsetDatetime = Annotated[
    datetime,
    PlainSerializer(datetime.isoformat, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
# `requested` is left out, because an appointment can only start there.
ChangeableStatus = Literal[
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.COMPLETED,
    AppointmentStatus.CANCELLED,
    AppointmentStatus.NO_SHOW,
]
# `rescheduled` is left out, because only a reschedule may record it.
StaffCancellationReason = Literal[
    CancellationReason.CLIENT_REQUEST,
    CancellationReason.PSYCHOLOGIST_REQUEST,
    CancellationReason.LATE_CANCELLATION,
    CancellationReason.OTHER,
]


class CreateAvailabilityRuleRequest(BaseModel):
    weekday: Weekday
    start_time: LocalTime
    end_time: LocalTime
    valid_from: date
    valid_until: date | None = None


class UpdateAvailabilityRuleRequest(PatchModel):
    nullable_fields = frozenset({"valid_until"})

    weekday: Weekday | None = None
    start_time: LocalTime | None = None
    end_time: LocalTime | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class AvailabilityRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    psychologist_id: uuid.UUID
    weekday: int
    start_time: time
    end_time: time
    valid_from: date
    valid_until: date | None
    created_at: datetime
    updated_at: datetime


class CreateTimeOffRequest(BaseModel):
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    reason: TimeOffReason | None = None


class UpdateTimeOffRequest(PatchModel):
    nullable_fields = frozenset({"reason"})

    starts_at: UtcDatetime | None = None
    ends_at: UtcDatetime | None = None
    reason: TimeOffReason | None = None


class TimeOffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    psychologist_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    reason: str | None
    created_at: datetime
    updated_at: datetime


class PublicProfileResponse(BaseModel):
    """An allow-list: a field reaches the public only by being named here."""

    model_config = ConfigDict(from_attributes=True)

    first_name: str
    last_name: str
    title: str | None
    slug: str
    bio: str | None
    timezone: str
    cancellation_window_hours: int
    services: list[OfferedServiceResponse]


class SlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    starts_at: OffsetDatetime
    ends_at: OffsetDatetime


class SlotsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timezone: str
    slots: list[SlotResponse]


class BookAppointmentRequest(BaseModel):
    psychologist_slug: ProfileSlug
    service_id: uuid.UUID
    starts_at: UtcDatetime


class RescheduleAppointmentRequest(BaseModel):
    starts_at: UtcDatetime


class CreateStaffAppointmentRequest(BaseModel):
    client_id: uuid.UUID
    service_id: uuid.UUID
    starts_at: UtcDatetime


class ChangeAppointmentStatusRequest(BaseModel):
    status: ChangeableStatus
    cancellation_reason: StaffCancellationReason | None = None

    @model_validator(mode="after")
    def reject_reason_without_cancellation(self) -> Self:
        if self.cancellation_reason is not None and self.status is not AppointmentStatus.CANCELLED:
            raise PydanticCustomError(
                "cancellation_reason_not_allowed", "Only a cancellation can have a reason."
            )
        return self


class ClientAppointmentResponse(BaseModel):
    """An allow-list: a client sees no staff IDs and nothing about how others booked."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    psychologist_name: str
    psychologist_slug: str
    service_name: str
    delivery_mode: DeliveryMode
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: AppointmentStatus
    price_amount: Decimal
    currency: str
    rescheduled_from_id: uuid.UUID | None
    cancelled_at: datetime | None
    cancellation_reason: CancellationReason | None
    created_at: datetime


class StaffAppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    psychologist_id: uuid.UUID
    client_id: uuid.UUID
    client_first_name: str
    client_last_name: str
    service_id: uuid.UUID
    service_name: str
    delivery_mode: DeliveryMode
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    price_amount: Decimal
    currency: str
    booking_channel: BookingChannel
    booked_by_user_id: uuid.UUID
    rescheduled_from_id: uuid.UUID | None
    cancelled_at: datetime | None
    cancelled_by_user_id: uuid.UUID | None
    cancellation_reason: CancellationReason | None
    created_at: datetime
    updated_at: datetime
