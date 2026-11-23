"""The single place that decides which appointment status changes are allowed."""

from collections.abc import Mapping
from datetime import datetime

from app.core.errors import AppError, ErrorCode
from app.modules.scheduling.models import AppointmentStatus


class InvalidStatusTransitionError(AppError):
    status_code = 409
    code = ErrorCode.INVALID_STATUS_TRANSITION
    message = "The appointment cannot change to this status."


class AppointmentNotStartedError(AppError):
    status_code = 409
    code = ErrorCode.APPOINTMENT_NOT_STARTED
    message = "The appointment has not started yet."


ALLOWED_TRANSITIONS: Mapping[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.REQUESTED: frozenset(
        {AppointmentStatus.CONFIRMED, AppointmentStatus.CANCELLED}
    ),
    AppointmentStatus.CONFIRMED: frozenset(
        {AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW}
    ),
    AppointmentStatus.COMPLETED: frozenset(),
    AppointmentStatus.CANCELLED: frozenset(),
    AppointmentStatus.NO_SHOW: frozenset(),
}
# Whether a client came can only be known once the session was due to begin.
REQUIRES_START = frozenset({AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW})


def ensure_transition_allowed(
    from_status: AppointmentStatus,
    to_status: AppointmentStatus,
    *,
    starts_at: datetime,
    now: datetime,
) -> None:
    if to_status not in ALLOWED_TRANSITIONS[from_status]:
        raise InvalidStatusTransitionError
    if to_status in REQUIRES_START and now < starts_at:
        raise AppointmentNotStartedError
