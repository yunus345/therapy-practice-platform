"""The demo data is realistic, consistent with the rules of the application, and repeatable."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise

import pytest

from app.modules.consent.models import ConsentType
from app.modules.scheduling.models import (
    ACTIVE_STATUSES,
    AppointmentStatus,
    BookingChannel,
    CancellationReason,
)
from app.modules.scheduling.state_machine import ALLOWED_TRANSITIONS
from app.modules.users.models import UserRole
from app.seed.fixtures import EMAIL_DOMAIN, PSYCHOLOGISTS, SERVICES
from app.seed.generator import TIMEZONE, DemoData, build_demo_data

TODAY = date(2026, 11, 20)
NOW = datetime.combine(TODAY, time(), TIMEZONE).astimezone(UTC)
CLOSED_STATUSES = {AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW}


@pytest.fixture(scope="module")
def data() -> DemoData:
    return build_demo_data(TODAY, "hash")


def test_the_same_day_gives_the_same_data(data: DemoData) -> None:
    again = build_demo_data(TODAY, "hash")

    def summary(built: DemoData) -> list[tuple[object, ...]]:
        return [(a.id, a.client_id, a.starts_at, a.status) for a in built.appointments]

    assert summary(again) == summary(data)
    assert [c.email for c in again.clients] == [c.email for c in data.clients]


def test_dates_follow_the_day_the_seed_runs(data: DemoData) -> None:
    later = build_demo_data(TODAY + timedelta(days=7), "hash")

    assert max(a.starts_at for a in later.appointments) > max(
        a.starts_at for a in data.appointments
    )


def test_the_amounts_match_the_spec(data: DemoData) -> None:
    roles = [user.role for user in data.users]
    clients_with_accounts = [client for client in data.clients if client.user_id]

    assert roles.count(UserRole.ADMIN) == 1
    assert roles.count(UserRole.PSYCHOLOGIST) == 2
    assert len(data.services) == len(SERVICES)
    assert 4 <= len(data.services) <= 6
    assert len(data.clients) == 80
    assert len(clients_with_accounts) == sum(spec.account_count for spec in PSYCHOLOGISTS)
    assert len(data.time_off) >= 4


def test_past_and_upcoming_appointments_span_the_requested_period(data: DemoData) -> None:
    starts = [appointment.starts_at for appointment in data.appointments]

    assert min(starts) < NOW - timedelta(weeks=25)
    assert max(starts) > NOW + timedelta(weeks=3)
    assert max(starts) < NOW + timedelta(weeks=4)
    assert sum(start < NOW for start in starts) > 500


def test_past_appointments_are_closed_and_upcoming_ones_are_open(data: DemoData) -> None:
    for appointment in data.appointments:
        if appointment.starts_at < NOW:
            assert appointment.status not in ACTIVE_STATUSES
        else:
            assert appointment.status not in CLOSED_STATUSES


def test_status_shares_are_realistic(data: DemoData) -> None:
    past = [a for a in data.appointments if a.starts_at < NOW]
    held = [a for a in past if a.status in CLOSED_STATUSES]
    no_shows = [a for a in held if a.status is AppointmentStatus.NO_SHOW]
    cancelled = [a for a in past if a.status is AppointmentStatus.CANCELLED]

    assert 0.03 < len(no_shows) / len(held) < 0.15
    assert 0.05 < len(cancelled) / len(past) < 0.25
    # `other` is for staff to pick by hand; the rules never produce it.
    assert {a.cancellation_reason for a in cancelled} == set(CancellationReason) - {
        CancellationReason.OTHER
    }


def test_active_appointments_never_overlap(data: DemoData) -> None:
    by_psychologist = defaultdict(list)
    for appointment in data.appointments:
        if appointment.status in ACTIVE_STATUSES:
            by_psychologist[appointment.psychologist_id].append(appointment)

    for appointments in by_psychologist.values():
        ordered = sorted(appointments, key=lambda a: a.starts_at)
        for earlier, later in pairwise(ordered):
            assert earlier.ends_at <= later.starts_at


def test_appointments_fall_in_working_hours_and_outside_time_off(data: DemoData) -> None:
    hours = {
        profile.user_id: spec.hours
        for profile, spec in zip(data.profiles, PSYCHOLOGISTS, strict=True)
    }
    for appointment in data.appointments:
        start = appointment.starts_at.astimezone(TIMEZONE)
        end = appointment.ends_at.astimezone(TIMEZONE)
        opens, closes = hours[appointment.psychologist_id][start.weekday()]
        assert opens <= start.time() and end.time() <= closes
        for period in data.time_off:
            if period.psychologist_id == appointment.psychologist_id:
                assert not (period.starts_at < appointment.ends_at < period.ends_at)
                assert not (period.starts_at <= appointment.starts_at < period.ends_at)


def test_bookings_happen_before_the_session_and_before_now(data: DemoData) -> None:
    for appointment in data.appointments:
        assert appointment.created_at < appointment.starts_at
        assert appointment.created_at < NOW


def test_online_bookings_respect_the_notice_and_come_from_the_client(data: DemoData) -> None:
    client_users = {client.id: client.user_id for client in data.clients}
    for appointment in data.appointments:
        if appointment.booking_channel is BookingChannel.ONLINE:
            assert appointment.starts_at - appointment.created_at >= timedelta(hours=24)
            assert appointment.booked_by_user_id == client_users[appointment.client_id]
        else:
            assert appointment.booked_by_user_id == appointment.psychologist_id


def test_history_replays_to_the_current_status(data: DemoData) -> None:
    history = defaultdict(list)
    for row in data.history:
        history[row.appointment_id].append(row)

    for appointment in data.appointments:
        rows = history[appointment.id]
        assert rows[0].from_status is None
        assert rows[0].changed_at == appointment.created_at
        for previous, row in pairwise(rows):
            assert row.from_status is previous.to_status
            assert row.to_status in ALLOWED_TRANSITIONS[previous.to_status]
            assert row.changed_at >= previous.changed_at
        assert rows[-1].to_status is appointment.status
        assert rows[-1].changed_at == appointment.updated_at


def test_cancellations_are_recorded_consistently(data: DemoData) -> None:
    for appointment in data.appointments:
        cancelled = appointment.status is AppointmentStatus.CANCELLED
        assert (appointment.cancelled_at is not None) is cancelled
        assert (appointment.cancellation_reason is not None) is cancelled
        if cancelled:
            assert appointment.cancelled_at is not None
            assert appointment.created_at <= appointment.cancelled_at < appointment.starts_at


def test_a_reschedule_points_to_a_session_cancelled_for_it(data: DemoData) -> None:
    by_id = {appointment.id: appointment for appointment in data.appointments}
    moved = [a for a in data.appointments if a.rescheduled_from_id is not None]

    assert moved
    for appointment in moved:
        assert appointment.rescheduled_from_id is not None
        original = by_id[appointment.rescheduled_from_id]
        assert original.cancellation_reason is CancellationReason.RESCHEDULED
        assert appointment.created_at == original.cancelled_at
        assert original.rescheduled_from_id is None


def test_every_client_has_a_disclosure_and_reaches_care(data: DemoData) -> None:
    disclosed = {
        c.client_id for c in data.consents if c.consent_type is ConsentType.KVKK_DISCLOSURE
    }
    in_care = {relationship.client_id for relationship in data.relationships}
    with_appointments = {appointment.client_id for appointment in data.appointments}

    assert disclosed == {client.id for client in data.clients}
    assert with_appointments <= in_care
    for relationship in data.relationships:
        if relationship.ended_at is not None:
            assert relationship.ended_at < NOW


def test_no_contact_detail_can_reach_a_real_person(data: DemoData) -> None:
    emails = [user.email for user in data.users] + [c.email for c in data.clients if c.email]

    assert all(email.endswith(f"@{EMAIL_DOMAIN}") for email in emails)
    assert len({user.email for user in data.users}) == len(data.users)
