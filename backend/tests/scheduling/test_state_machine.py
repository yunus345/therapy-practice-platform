from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.modules.scheduling.models import AppointmentStatus
from app.modules.scheduling.state_machine import (
    AppointmentNotStartedError,
    InvalidStatusTransitionError,
    ensure_transition_allowed,
)

REQUESTED = AppointmentStatus.REQUESTED
CONFIRMED = AppointmentStatus.CONFIRMED
COMPLETED = AppointmentStatus.COMPLETED
CANCELLED = AppointmentStatus.CANCELLED
NO_SHOW = AppointmentStatus.NO_SHOW

ALLOWED = {
    (REQUESTED, CONFIRMED),
    (REQUESTED, CANCELLED),
    (CONFIRMED, COMPLETED),
    (CONFIRMED, CANCELLED),
    (CONFIRMED, NO_SHOW),
}
ALL_PAIRS = set(product(AppointmentStatus, repeat=2))
DISALLOWED = ALL_PAIRS - ALLOWED
NEEDS_START = {(CONFIRMED, COMPLETED), (CONFIRMED, NO_SHOW)}

STARTS_AT = datetime(2026, 12, 8, 9, 0, tzinfo=UTC)
BEFORE_START = STARTS_AT - timedelta(microseconds=1)
AFTER_START = STARTS_AT + timedelta(minutes=10)


def pair_id(pair: tuple[AppointmentStatus, AppointmentStatus]) -> str:
    return f"{pair[0]}-to-{pair[1]}"


def test_the_table_covers_every_pair_of_statuses() -> None:
    assert len(ALL_PAIRS) == len(AppointmentStatus) ** 2 == 25
    assert len(DISALLOWED) == 20


@pytest.mark.parametrize("pair", sorted(ALLOWED), ids=pair_id)
@pytest.mark.parametrize("now", [STARTS_AT, AFTER_START], ids=["at_start", "after_start"])
def test_allowed_transitions_pass_once_the_appointment_has_started(
    pair: tuple[AppointmentStatus, AppointmentStatus], now: datetime
) -> None:
    ensure_transition_allowed(pair[0], pair[1], starts_at=STARTS_AT, now=now)


@pytest.mark.parametrize("pair", sorted(ALLOWED - NEEDS_START), ids=pair_id)
def test_confirming_and_cancelling_do_not_wait_for_the_start(
    pair: tuple[AppointmentStatus, AppointmentStatus],
) -> None:
    ensure_transition_allowed(pair[0], pair[1], starts_at=STARTS_AT, now=BEFORE_START)


@pytest.mark.parametrize("pair", sorted(NEEDS_START), ids=pair_id)
def test_recording_attendance_before_the_start_is_refused(
    pair: tuple[AppointmentStatus, AppointmentStatus],
) -> None:
    with pytest.raises(AppointmentNotStartedError):
        ensure_transition_allowed(pair[0], pair[1], starts_at=STARTS_AT, now=BEFORE_START)


@pytest.mark.parametrize("pair", sorted(DISALLOWED), ids=pair_id)
@pytest.mark.parametrize("now", [BEFORE_START, AFTER_START], ids=["before_start", "after_start"])
def test_every_other_pair_is_refused_at_any_time(
    pair: tuple[AppointmentStatus, AppointmentStatus], now: datetime
) -> None:
    with pytest.raises(InvalidStatusTransitionError):
        ensure_transition_allowed(pair[0], pair[1], starts_at=STARTS_AT, now=now)


def test_errors_carry_their_codes() -> None:
    assert InvalidStatusTransitionError.status_code == 409
    assert InvalidStatusTransitionError.code == "INVALID_STATUS_TRANSITION"
    assert AppointmentNotStartedError.status_code == 409
    assert AppointmentNotStartedError.code == "APPOINTMENT_NOT_STARTED"
