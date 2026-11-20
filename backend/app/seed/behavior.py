"""The rules that decide how a synthetic session turns out.

Phase 5 trains a no-show model on the seed data. Every pattern such a model can find in
that data is decided here, so the model can be judged against the truth: it should
recover these effects, and anything more it claims is noise. Change a rule only together
with the ADR that documents it (ADR-005).

Each outcome has a score in log-odds: a base value plus the effects that apply. The
logistic function turns the score into a probability. An effect of +0.7 about doubles
the odds of the outcome, and -0.7 about halves them.

Outcomes are drawn in this order, each from the sessions the previous ones left over:

1. Early cancellation: more than the cancellation window before the start.
2. Late cancellation: inside the cancellation window.
3. No-show.
4. Otherwise the session is completed.
"""

import math
import random
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

# Hours of the local clock that clients find hard to keep: before 11:00 and from 18:00.
EARLY_HOUR_LIMIT = 11
LATE_HOUR_START = 18

LONG_LEAD = timedelta(days=14)
MEDIUM_LEAD = timedelta(days=7)
# A cancellation needs some time between booking and start to fit in.
MIN_CANCELLATION_ROOM = timedelta(hours=1)
# Prior events stop adding to the score after this many.
PRIOR_EVENT_CAP = 2

# No-show: about 5% for a returning client with no risk factor.
NO_SHOW_BASE = -3.0
NO_SHOW_PER_PRIOR_NO_SHOW = 0.8
NO_SHOW_FIRST_SESSION = 0.7
NO_SHOW_LONG_LEAD = 0.6
NO_SHOW_MEDIUM_LEAD = 0.3
NO_SHOW_EDGE_HOUR = 0.4
# Weekday 0 is Monday. Days not listed add nothing.
NO_SHOW_WEEKDAY = {0: 0.3, 4: 0.2, 5: 0.4}
NO_SHOW_ONLINE_SESSION = -0.5
NO_SHOW_BOOKED_ONLINE = 0.2

# Late cancellation: about 3.5% at base.
LATE_CANCELLATION_BASE = -3.3
LATE_CANCELLATION_PER_PRIOR = 0.6
LATE_CANCELLATION_LONG_LEAD = 0.4
LATE_CANCELLATION_WEEKDAY = {0: 0.3, 5: 0.3}

# Early cancellation: about 6% at base.
EARLY_CANCELLATION_BASE = -2.7
EARLY_CANCELLATION_LONG_LEAD = 0.7
EARLY_CANCELLATION_MEDIUM_LEAD = 0.3
EARLY_CANCELLATION_FIRST_SESSION = 0.4

# How an early cancellation is split. The psychologist cancels some; clients move some of
# the rest to another time instead of dropping them.
PSYCHOLOGIST_CANCELLATION_SHARE = 0.15
RESCHEDULE_SHARE = 0.4

# After each no-show, the chance that a client stops coming altogether.
DROPOUT_AFTER_NO_SHOW = 0.3


class Outcome(StrEnum):
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    LATE_CANCELLATION = "late_cancellation"
    EARLY_CANCELLATION = "early_cancellation"


class EarlyCancellationKind(StrEnum):
    CLIENT = "client"
    PSYCHOLOGIST = "psychologist"
    RESCHEDULE = "reschedule"


@dataclass(frozen=True)
class SessionContext:
    """What is known about a session when it is booked."""

    # From the booking to the start of the session.
    lead_time: timedelta
    cancellation_window: timedelta
    # On the psychologist's local clock; weekday 0 is Monday.
    weekday: int
    local_hour: int
    # The client has not attended a session yet.
    is_first_session: bool
    prior_no_shows: int
    prior_late_cancellations: int
    # Booked by the client through the portal rather than by staff.
    booked_online: bool
    online_session: bool


def _logistic(score: float) -> float:
    return 1 / (1 + math.exp(-score))


def _lead_effect(lead_time: timedelta, long: float, medium: float = 0.0) -> float:
    if lead_time > LONG_LEAD:
        return long
    if lead_time > MEDIUM_LEAD:
        return medium
    return 0.0


def no_show_probability(context: SessionContext) -> float:
    score = NO_SHOW_BASE
    score += NO_SHOW_PER_PRIOR_NO_SHOW * min(context.prior_no_shows, PRIOR_EVENT_CAP)
    score += NO_SHOW_FIRST_SESSION if context.is_first_session else 0.0
    score += _lead_effect(context.lead_time, NO_SHOW_LONG_LEAD, NO_SHOW_MEDIUM_LEAD)
    edge_hour = context.local_hour < EARLY_HOUR_LIMIT or context.local_hour >= LATE_HOUR_START
    score += NO_SHOW_EDGE_HOUR if edge_hour else 0.0
    score += NO_SHOW_WEEKDAY.get(context.weekday, 0.0)
    score += NO_SHOW_ONLINE_SESSION if context.online_session else 0.0
    score += NO_SHOW_BOOKED_ONLINE if context.booked_online else 0.0
    return _logistic(score)


def late_cancellation_probability(context: SessionContext) -> float:
    if context.lead_time <= MIN_CANCELLATION_ROOM:
        return 0.0
    score = LATE_CANCELLATION_BASE
    prior = min(context.prior_late_cancellations, PRIOR_EVENT_CAP)
    score += LATE_CANCELLATION_PER_PRIOR * prior
    score += _lead_effect(context.lead_time, LATE_CANCELLATION_LONG_LEAD)
    score += LATE_CANCELLATION_WEEKDAY.get(context.weekday, 0.0)
    return _logistic(score)


def early_cancellation_probability(context: SessionContext) -> float:
    if context.lead_time <= context.cancellation_window + MIN_CANCELLATION_ROOM:
        return 0.0
    score = EARLY_CANCELLATION_BASE
    score += _lead_effect(
        context.lead_time, EARLY_CANCELLATION_LONG_LEAD, EARLY_CANCELLATION_MEDIUM_LEAD
    )
    score += EARLY_CANCELLATION_FIRST_SESSION if context.is_first_session else 0.0
    return _logistic(score)


def decide_outcome(context: SessionContext, rng: random.Random) -> Outcome:
    if rng.random() < early_cancellation_probability(context):
        return Outcome.EARLY_CANCELLATION
    if rng.random() < late_cancellation_probability(context):
        return Outcome.LATE_CANCELLATION
    if rng.random() < no_show_probability(context):
        return Outcome.NO_SHOW
    return Outcome.COMPLETED


def decide_early_cancellation(rng: random.Random, *, can_reschedule: bool) -> EarlyCancellationKind:
    """Who cancels early, and whether the client moves the session instead."""
    if rng.random() < PSYCHOLOGIST_CANCELLATION_SHARE:
        return EarlyCancellationKind.PSYCHOLOGIST
    if can_reschedule and rng.random() < RESCHEDULE_SHARE:
        return EarlyCancellationKind.RESCHEDULE
    return EarlyCancellationKind.CLIENT


def drops_out(rng: random.Random) -> bool:
    """Whether a client stops therapy after a no-show."""
    return rng.random() < DROPOUT_AFTER_NO_SHOW
