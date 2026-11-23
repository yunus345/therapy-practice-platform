"""Slot calculation: pure functions, with no database and no clock.

Every comparison uses UTC instants. Local wall-clock time matters only where a weekly
rule becomes a working window on a local date.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WeeklyRule:
    """Working hours on the local clock. Weekday 0 is Monday, as in `date.weekday()`."""

    weekday: int
    start_time: time
    end_time: time
    valid_from: date
    valid_until: date | None

    def applies_on(self, day: date) -> bool:
        return (
            day.weekday() == self.weekday
            and self.valid_from <= day
            and (self.valid_until is None or day <= self.valid_until)
        )


@dataclass(frozen=True)
class Interval:
    """A half-open period, so that one interval may end exactly where the next starts."""

    starts_at: datetime
    ends_at: datetime

    def overlaps(self, other: Interval) -> bool:
        return self.starts_at < other.ends_at and self.ends_at > other.starts_at


@dataclass(frozen=True)
class SlotInputs:
    """Everything a slot depends on. `from_date` and `to_date` are inclusive local dates."""

    rules: Sequence[WeeklyRule]
    zone: ZoneInfo
    duration: timedelta
    buffer: timedelta
    granularity: timedelta
    not_before: datetime
    not_after: datetime
    from_date: date
    to_date: date
    appointments: Sequence[Interval]
    time_off: Sequence[Interval]


def _to_utc(day: date, clock_time: time, zone: ZoneInfo) -> datetime:
    """Convert a local date and time. DST gaps and folds resolve through `fold=0`."""
    return datetime.combine(day, clock_time, tzinfo=zone).astimezone(UTC)


def local_period(from_date: date, to_date: date, zone: ZoneInfo) -> Interval:
    """Return a period that holds every working window of a range of local dates."""
    return Interval(_to_utc(from_date, time.min, zone), _to_utc(to_date, time.max, zone))


def _working_windows(inputs: SlotInputs, day: date) -> list[Interval]:
    """Turn the rules of a local date into UTC windows, merging those that overlap or touch.

    A rule that starts inside a DST gap can end at or before its own start. Such a window
    needs no special case: it merges with nothing that comes later and fits no session.
    """
    zone = inputs.zone
    converted = (
        Interval(_to_utc(day, rule.start_time, zone), _to_utc(day, rule.end_time, zone))
        for rule in inputs.rules
        if rule.applies_on(day)
    )
    merged: list[Interval] = []
    for window in sorted(converted, key=lambda item: item.starts_at):
        if merged and window.starts_at <= merged[-1].ends_at:
            merged[-1] = Interval(merged[-1].starts_at, max(merged[-1].ends_at, window.ends_at))
        else:
            merged.append(window)
    return merged


def _candidates(inputs: SlotInputs, window: Interval) -> Iterator[datetime]:
    """Step through a window from its start. Busy periods never shift this grid."""
    starts_at = window.starts_at
    while starts_at + inputs.duration <= window.ends_at:
        yield starts_at
        starts_at += inputs.granularity


def _is_free(inputs: SlotInputs, starts_at: datetime) -> bool:
    session = Interval(starts_at, starts_at + inputs.duration)
    # Only the buffer may run past the working window, and it does not apply to time off.
    buffered = Interval(session.starts_at - inputs.buffer, session.ends_at + inputs.buffer)
    return (
        inputs.not_before <= starts_at <= inputs.not_after
        and not any(session.overlaps(period) for period in inputs.time_off)
        and not any(buffered.overlaps(period) for period in inputs.appointments)
    )


def available_starts(inputs: SlotInputs) -> list[datetime]:
    """Return the sorted, unique UTC starts of the slots on the local dates of `inputs`."""
    days = (inputs.to_date - inputs.from_date).days + 1
    return sorted(
        {
            starts_at
            for offset in range(days)
            for window in _working_windows(inputs, inputs.from_date + timedelta(days=offset))
            for starts_at in _candidates(inputs, window)
            if _is_free(inputs, starts_at)
        }
    )


def is_available_start(inputs: SlotInputs, starts_at: datetime) -> bool:
    """Tell whether a start is one of the slots of `inputs`, whatever its UTC offset."""
    return starts_at.astimezone(UTC) in available_starts(inputs)
