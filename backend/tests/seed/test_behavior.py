"""The generating rules that the future no-show model will be judged against."""

import random
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.seed import behavior
from app.seed.behavior import (
    EarlyCancellationKind,
    Outcome,
    SessionContext,
    decide_early_cancellation,
    decide_outcome,
    early_cancellation_probability,
    late_cancellation_probability,
    no_show_probability,
)


def seeded_rng() -> random.Random:
    return random.Random(1)  # noqa: S311


# A returning client on a Wednesday afternoon, booked a few days ahead by staff.
BASELINE = SessionContext(
    lead_time=timedelta(days=3),
    cancellation_window=timedelta(hours=24),
    weekday=2,
    local_hour=14,
    is_first_session=False,
    prior_no_shows=0,
    prior_late_cancellations=0,
    booked_online=False,
    online_session=False,
)


def test_the_baseline_rates_match_the_documented_values() -> None:
    assert no_show_probability(BASELINE) == pytest.approx(0.047, abs=0.001)
    assert late_cancellation_probability(BASELINE) == pytest.approx(0.036, abs=0.001)
    assert early_cancellation_probability(BASELINE) == pytest.approx(0.063, abs=0.001)


@pytest.mark.parametrize(
    "changes",
    [
        {"prior_no_shows": 1},
        {"is_first_session": True},
        {"lead_time": timedelta(days=10)},
        {"lead_time": timedelta(days=20)},
        {"local_hour": 10},
        {"local_hour": 18},
        {"weekday": 0},
        {"weekday": 4},
        {"weekday": 5},
        {"booked_online": True},
    ],
)
def test_risk_factors_raise_the_no_show_probability(changes: dict[str, Any]) -> None:
    assert no_show_probability(replace(BASELINE, **changes)) > no_show_probability(BASELINE)


def test_an_online_session_lowers_the_no_show_probability() -> None:
    online = replace(BASELINE, online_session=True)

    assert no_show_probability(online) < no_show_probability(BASELINE)


def test_prior_events_stop_counting_at_the_cap() -> None:
    two = replace(BASELINE, prior_no_shows=2, prior_late_cancellations=2)
    five = replace(BASELINE, prior_no_shows=5, prior_late_cancellations=5)

    assert no_show_probability(five) == no_show_probability(two)
    assert late_cancellation_probability(five) == late_cancellation_probability(two)


def test_a_late_cancellation_needs_time_between_booking_and_start() -> None:
    rushed = replace(BASELINE, lead_time=timedelta(minutes=50))

    assert late_cancellation_probability(rushed) == 0


def test_an_early_cancellation_needs_time_before_the_window() -> None:
    inside = replace(BASELINE, lead_time=timedelta(hours=24, minutes=30))

    assert early_cancellation_probability(inside) == 0
    assert late_cancellation_probability(inside) > 0


def test_outcomes_follow_their_probabilities() -> None:
    rng = seeded_rng()
    risky = replace(BASELINE, prior_no_shows=2, is_first_session=True, weekday=5)
    draws = 20_000

    outcomes = [decide_outcome(risky, rng) for _ in range(draws)]

    no_show_share = outcomes.count(Outcome.NO_SHOW) / draws
    # A no-show is drawn only from the sessions that no cancellation took.
    remaining = (1 - early_cancellation_probability(risky)) * (
        1 - late_cancellation_probability(risky)
    )
    assert no_show_share == pytest.approx(no_show_probability(risky) * remaining, abs=0.01)


def test_a_reschedule_is_never_rescheduled_again() -> None:
    rng = seeded_rng()

    kinds = {decide_early_cancellation(rng, can_reschedule=False) for _ in range(500)}

    assert kinds == {EarlyCancellationKind.CLIENT, EarlyCancellationKind.PSYCHOLOGIST}


def test_every_kind_of_early_cancellation_occurs() -> None:
    rng = seeded_rng()

    kinds = {decide_early_cancellation(rng, can_reschedule=True) for _ in range(500)}

    assert kinds == set(EarlyCancellationKind)


def test_dropout_follows_its_rate() -> None:
    rng = seeded_rng()

    share = sum(behavior.drops_out(rng) for _ in range(10_000)) / 10_000

    assert share == pytest.approx(behavior.DROPOUT_AFTER_NO_SHOW, abs=0.02)
