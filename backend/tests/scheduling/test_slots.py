from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.modules.scheduling.slots import (
    Interval,
    SlotInputs,
    WeeklyRule,
    available_starts,
    is_available_start,
    local_period,
)

MONDAY = date(2026, 12, 7)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
MONDAY_WEEKDAY = 0
TUESDAY_WEEKDAY = 1
WEDNESDAY_WEEKDAY = 2
SUNDAY_WEEKDAY = 6

UTC_ZONE = ZoneInfo("UTC")
ISTANBUL = ZoneInfo("Europe/Istanbul")
NEW_YORK = ZoneInfo("America/New_York")
NUUK = ZoneInfo("America/Nuuk")

# America/New_York: clocks jump from 02:00 to 03:00, then fall from 02:00 back to 01:00.
DST_GAP = date(2026, 3, 8)
DST_FOLD = date(2026, 11, 1)


def minutes(amount: int) -> timedelta:
    return timedelta(minutes=amount)


def rule(
    start: str = "09:00",
    end: str = "17:00",
    *,
    weekday: int = MONDAY_WEEKDAY,
    valid_from: date = date(2000, 1, 1),
    valid_until: date | None = None,
) -> WeeklyRule:
    return WeeklyRule(
        weekday=weekday,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        valid_from=valid_from,
        valid_until=valid_until,
    )


def inputs(*rules: WeeklyRule, **overrides: Any) -> SlotInputs:
    """Monday in UTC, with a 50 minute session, a 15 minute grid and no limits."""
    values: dict[str, Any] = {
        "rules": rules,
        "zone": UTC_ZONE,
        "duration": minutes(50),
        "buffer": minutes(0),
        "granularity": minutes(15),
        "not_before": datetime(2000, 1, 1, tzinfo=UTC),
        "not_after": datetime(2100, 1, 1, tzinfo=UTC),
        "from_date": MONDAY,
        "to_date": MONDAY,
        "appointments": (),
        "time_off": (),
    }
    return SlotInputs(**(values | overrides))


def at(clock_time: str, day: date = MONDAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(clock_time), tzinfo=UTC)


def between(start: str, end: str, day: date = MONDAY) -> Interval:
    return Interval(at(start, day), at(end, day))


def clock_times(slot_inputs: SlotInputs) -> list[str]:
    return [start.strftime("%H:%M") for start in available_starts(slot_inputs)]


class TestWorkingHours:
    def test_starts_step_through_the_window_until_the_session_no_longer_fits(self) -> None:
        assert clock_times(inputs(rule("09:00", "10:30"))) == ["09:00", "09:15", "09:30"]

    def test_there_are_no_slots_without_rules(self) -> None:
        assert available_starts(inputs()) == []

    def test_weekday_zero_is_monday(self) -> None:
        assert MONDAY.strftime("%A") == "Monday"
        assert available_starts(inputs(rule("09:00", "10:00", weekday=0)))

    def test_a_rule_for_another_weekday_gives_no_slots(self) -> None:
        assert available_starts(inputs(rule(weekday=TUESDAY_WEEKDAY))) == []

    def test_both_ends_of_the_date_range_are_included(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "09:50"),
            rule("10:00", "10:50", weekday=TUESDAY_WEEKDAY),
            rule("11:00", "11:50", weekday=WEDNESDAY_WEEKDAY),
            from_date=MONDAY,
            to_date=WEDNESDAY,
        )

        assert available_starts(slot_inputs) == [
            at("09:00", MONDAY),
            at("10:00", TUESDAY),
            at("11:00", WEDNESDAY),
        ]

    def test_the_same_rule_repeats_every_week(self) -> None:
        next_monday = MONDAY + timedelta(days=7)

        starts = available_starts(inputs(rule("09:00", "09:50"), to_date=next_monday))

        assert starts == [at("09:00", MONDAY), at("09:00", next_monday)]

    def test_a_reversed_date_range_has_no_slots(self) -> None:
        assert available_starts(inputs(rule(), from_date=TUESDAY, to_date=MONDAY)) == []

    def test_results_are_sorted_whatever_the_order_of_the_rules(self) -> None:
        assert clock_times(inputs(rule("13:00", "13:50"), rule("09:00", "09:50"))) == [
            "09:00",
            "13:00",
        ]


class TestRuleValidity:
    @pytest.mark.parametrize(
        ("valid_from", "valid_until", "applies"),
        [
            (MONDAY, None, True),
            (TUESDAY, None, False),
            (MONDAY - timedelta(days=7), MONDAY, True),
            (MONDAY - timedelta(days=7), MONDAY - timedelta(days=1), False),
            (MONDAY, MONDAY, True),
        ],
        ids=[
            "starts_that_day",
            "starts_a_day_later",
            "ends_that_day",
            "ended_a_day_earlier",
            "valid_for_one_day",
        ],
    )
    def test_both_validity_dates_are_inclusive(
        self, valid_from: date, valid_until: date | None, applies: bool
    ) -> None:
        slot_inputs = inputs(rule(valid_from=valid_from, valid_until=valid_until))

        assert bool(available_starts(slot_inputs)) is applies

    def test_validity_is_checked_for_each_date(self) -> None:
        next_monday = MONDAY + timedelta(days=7)
        slot_inputs = inputs(
            rule("09:00", "09:50", valid_until=MONDAY + timedelta(days=1)), to_date=next_monday
        )

        assert available_starts(slot_inputs) == [at("09:00", MONDAY)]


class TestMergingRules:
    def test_touching_rules_act_as_one_window(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"), rule("10:00", "11:00"), duration=minutes(120))

        assert clock_times(slot_inputs) == ["09:00"]

    def test_overlapping_rules_act_as_one_window(self) -> None:
        slot_inputs = inputs(rule("09:00", "11:00"), rule("10:00", "12:00"), duration=minutes(180))

        assert clock_times(slot_inputs) == ["09:00"]

    def test_a_rule_inside_another_changes_nothing(self) -> None:
        contained = inputs(rule("09:00", "12:00"), rule("10:00", "11:00"))

        assert available_starts(contained) == available_starts(inputs(rule("09:00", "12:00")))

    def test_rules_merge_whatever_their_order(self) -> None:
        slot_inputs = inputs(rule("10:00", "11:00"), rule("09:00", "10:00"), duration=minutes(120))

        assert clock_times(slot_inputs) == ["09:00"]

    def test_the_grid_is_anchored_at_the_start_of_the_merged_window(self) -> None:
        slot_inputs = inputs(rule("09:10", "10:00"), rule("10:00", "10:50"), duration=minutes(30))

        assert clock_times(slot_inputs) == [
            "09:10",
            "09:25",
            "09:40",
            "09:55",
            "10:10",
        ]

    def test_separate_windows_each_anchor_their_own_grid(self) -> None:
        slot_inputs = inputs(rule("09:00", "09:50"), rule("13:05", "13:55"))

        assert clock_times(slot_inputs) == ["09:00", "13:05"]

    def test_a_gap_between_rules_is_not_bridged(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"), rule("10:01", "11:00"), duration=minutes(120))

        assert available_starts(slot_inputs) == []

    def test_a_rule_out_of_validity_does_not_merge(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"),
            rule("10:00", "11:00", valid_until=MONDAY - timedelta(days=1)),
            duration=minutes(120),
        )

        assert available_starts(slot_inputs) == []

    def test_rules_of_different_dates_do_not_merge(self) -> None:
        slot_inputs = inputs(
            rule("23:00", "23:59"),
            rule("00:00", "01:00", weekday=TUESDAY_WEEKDAY),
            to_date=TUESDAY,
            duration=minutes(90),
        )

        assert available_starts(slot_inputs) == []


class TestTimeOff:
    def test_a_slot_that_overlaps_time_off_is_removed(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "12:00"),
            duration=minutes(30),
            time_off=[between("10:00", "11:00")],
        )

        assert clock_times(slot_inputs) == ["09:00", "09:15", "09:30", "11:00", "11:15", "11:30"]

    def test_the_buffer_does_not_apply_to_time_off(self) -> None:
        without_buffer = inputs(
            rule("09:00", "12:00"), duration=minutes(30), time_off=[between("10:00", "11:00")]
        )

        with_buffer = replace(without_buffer, buffer=minutes(30))

        assert available_starts(with_buffer) == available_starts(without_buffer)

    def test_time_off_over_the_whole_day_leaves_nothing(self) -> None:
        slot_inputs = inputs(rule(), time_off=[between("00:00", "23:59")])

        assert available_starts(slot_inputs) == []

    def test_time_off_on_another_day_changes_nothing(self) -> None:
        slot_inputs = inputs(rule("09:00", "09:50"), time_off=[between("09:00", "10:00", TUESDAY)])

        assert clock_times(slot_inputs) == ["09:00"]

    def test_periods_in_another_timezone_are_compared_as_instants(self) -> None:
        period = Interval(
            at("10:00").astimezone(ISTANBUL),
            at("11:00").astimezone(ISTANBUL),
        )
        slot_inputs = inputs(rule("09:00", "12:00"), duration=minutes(30), time_off=[period])

        assert clock_times(slot_inputs) == ["09:00", "09:15", "09:30", "11:00", "11:15", "11:30"]


class TestAppointments:
    def test_without_a_buffer_a_slot_can_touch_an_appointment(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "12:00"),
            duration=minutes(30),
            appointments=[between("10:00", "11:00")],
        )

        assert clock_times(slot_inputs) == ["09:00", "09:15", "09:30", "11:00", "11:15", "11:30"]

    def test_the_buffer_keeps_slots_away_on_both_sides(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "12:00"),
            duration=minutes(30),
            buffer=minutes(15),
            appointments=[between("10:00", "11:00")],
        )

        assert clock_times(slot_inputs) == ["09:00", "09:15", "11:15", "11:30"]

    def test_a_gap_of_exactly_the_buffer_is_enough(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"),
            duration=minutes(60),
            buffer=minutes(60),
            appointments=[between("07:00", "08:00"), between("11:00", "12:00")],
        )

        assert clock_times(slot_inputs) == ["09:00"]

    @pytest.mark.parametrize(
        "appointment",
        [between("07:00", "08:00:01"), between("10:59:59", "12:00")],
        ids=["ends_too_late", "starts_too_early"],
    )
    def test_a_gap_a_second_shorter_than_the_buffer_is_not(self, appointment: Interval) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"),
            duration=minutes(60),
            buffer=minutes(60),
            appointments=[appointment],
        )

        assert available_starts(slot_inputs) == []

    def test_the_grid_is_not_anchored_again_after_a_busy_period(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "11:00"),
            duration=minutes(30),
            appointments=[between("09:00", "09:10")],
        )

        assert clock_times(slot_inputs)[0] == "09:15"

    def test_the_last_buffer_of_a_window_may_run_past_its_end(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"), duration=minutes(60), buffer=minutes(30))

        assert clock_times(slot_inputs) == ["09:00"]

    def test_an_appointment_outside_working_hours_still_blocks_through_the_buffer(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"),
            duration=minutes(30),
            buffer=minutes(15),
            appointments=[between("08:00", "08:50")],
        )

        assert clock_times(slot_inputs) == ["09:15", "09:30"]

    def test_the_buffer_is_real_time_for_periods_in_another_timezone(self) -> None:
        # Adding two hours to 01:45 on the New York clock would skip the DST gap.
        ends_at = at("06:45", DST_GAP).astimezone(NEW_YORK)
        slot_inputs = inputs(
            rule("07:00", "10:00", weekday=SUNDAY_WEEKDAY),
            from_date=DST_GAP,
            to_date=DST_GAP,
            duration=minutes(30),
            buffer=minutes(120),
            appointments=[Interval(ends_at - minutes(30), ends_at)],
        )

        assert clock_times(slot_inputs) == ["08:45", "09:00", "09:15", "09:30"]


class TestBookingLimits:
    def test_both_limits_are_inclusive(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "11:00"),
            duration=minutes(30),
            not_before=at("09:30"),
            not_after=at("10:00"),
        )

        assert clock_times(slot_inputs) == ["09:30", "09:45", "10:00"]

    def test_a_start_one_second_early_is_removed(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"), duration=minutes(30), not_before=at("09:00:01")
        )

        assert clock_times(slot_inputs) == ["09:15", "09:30"]

    def test_a_start_one_second_late_is_removed(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"), duration=minutes(30), not_after=at("09:14:59"))

        assert clock_times(slot_inputs) == ["09:00"]

    def test_limits_outside_the_date_range_change_nothing(self) -> None:
        unlimited = inputs(rule("09:00", "10:00"))
        limited = replace(unlimited, not_before=at("00:00"), not_after=at("23:59"))

        assert available_starts(limited) == available_starts(unlimited)


class TestDurationAndGranularity:
    def test_a_session_that_exactly_fills_the_window_fits(self) -> None:
        assert clock_times(inputs(rule("09:00", "09:50"))) == ["09:00"]

    def test_a_session_a_minute_longer_than_the_window_does_not_fit(self) -> None:
        assert available_starts(inputs(rule("09:00", "09:49"))) == []

    @pytest.mark.parametrize(
        ("granularity", "expected"),
        [
            (5, ["09:00", "09:05", "09:10", "09:15", "09:20", "09:25", "09:30"]),
            (10, ["09:00", "09:10", "09:20", "09:30"]),
            (30, ["09:00", "09:30"]),
            (60, ["09:00"]),
        ],
    )
    def test_granularity_sets_the_step(self, granularity: int, expected: list[str]) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"), duration=minutes(30), granularity=minutes(granularity)
        )

        assert clock_times(slot_inputs) == expected

    def test_a_step_longer_than_the_session_leaves_time_between_starts(self) -> None:
        slot_inputs = inputs(rule("09:00", "12:00"), duration=minutes(30), granularity=minutes(60))

        assert clock_times(slot_inputs) == ["09:00", "10:00", "11:00"]


class TestTimezones:
    def test_starts_are_utc_aware(self) -> None:
        starts = available_starts(
            inputs(
                rule("09:00", "10:00", weekday=TUESDAY_WEEKDAY),
                zone=ISTANBUL,
                from_date=TUESDAY,
                to_date=TUESDAY,
            )
        )

        assert starts
        assert all(start.tzinfo is UTC for start in starts)

    def test_a_local_morning_falls_on_the_previous_utc_date(self) -> None:
        slot_inputs = inputs(
            rule("01:00", "02:30", weekday=TUESDAY_WEEKDAY),
            zone=ISTANBUL,
            from_date=TUESDAY,
            to_date=TUESDAY,
        )

        assert available_starts(slot_inputs) == [
            at("22:00", MONDAY),
            at("22:15", MONDAY),
            at("22:30", MONDAY),
        ]

    def test_the_weekday_and_validity_follow_the_local_date(self) -> None:
        slot_inputs = inputs(
            rule("01:00", "01:50", weekday=TUESDAY_WEEKDAY, valid_from=TUESDAY),
            zone=ISTANBUL,
            from_date=TUESDAY,
            to_date=TUESDAY,
        )

        assert available_starts(slot_inputs) == [at("22:00", MONDAY)]

    def test_the_date_range_means_local_dates(self) -> None:
        slot_inputs = inputs(
            rule("01:00", "01:50", weekday=TUESDAY_WEEKDAY),
            zone=ISTANBUL,
            from_date=MONDAY,
            to_date=MONDAY,
        )

        assert available_starts(slot_inputs) == []

    def test_a_local_midnight_window_lands_before_utc_midnight(self) -> None:
        slot_inputs = inputs(
            rule("00:00", "00:50", weekday=TUESDAY_WEEKDAY),
            zone=ISTANBUL,
            from_date=TUESDAY,
            to_date=TUESDAY,
        )

        assert available_starts(slot_inputs) == [at("21:00", MONDAY)]

    def test_the_end_of_a_local_day_stays_on_that_utc_date(self) -> None:
        slot_inputs = inputs(rule("23:00", "23:50"), zone=ISTANBUL)

        assert available_starts(slot_inputs) == [at("20:00", MONDAY)]

    def test_the_grid_runs_across_the_utc_date(self) -> None:
        slot_inputs = inputs(
            rule("02:00", "04:00", weekday=TUESDAY_WEEKDAY),
            zone=ISTANBUL,
            from_date=TUESDAY,
            to_date=TUESDAY,
            duration=minutes(30),
            granularity=minutes(30),
        )

        assert available_starts(slot_inputs) == [
            at("23:00", MONDAY),
            at("23:30", MONDAY),
            at("00:00", TUESDAY),
            at("00:30", TUESDAY),
        ]

    @pytest.mark.parametrize(
        ("day", "expected_hour"),
        [
            (DST_GAP - timedelta(days=7), 14),
            (DST_GAP + timedelta(days=7), 13),
            (DST_FOLD - timedelta(days=7), 13),
            (DST_FOLD + timedelta(days=7), 14),
        ],
        ids=["before_the_gap", "after_the_gap", "before_the_fold", "after_the_fold"],
    )
    def test_local_time_stays_fixed_while_the_utc_time_moves(
        self, day: date, expected_hour: int
    ) -> None:
        slot_inputs = inputs(
            rule("09:00", "09:50", weekday=SUNDAY_WEEKDAY),
            zone=NEW_YORK,
            from_date=day,
            to_date=day,
        )

        assert [start.hour for start in available_starts(slot_inputs)] == [expected_hour]


class TestDstGap:
    @staticmethod
    def gap_inputs(*rules: WeeklyRule, **overrides: Any) -> SlotInputs:
        return inputs(
            *rules, **({"zone": NEW_YORK, "from_date": DST_GAP, "to_date": DST_GAP} | overrides)
        )

    @staticmethod
    def utc(clock_time: str) -> datetime:
        return at(clock_time, DST_GAP)

    def test_a_window_across_the_gap_is_an_hour_shorter(self) -> None:
        slot_inputs = self.gap_inputs(
            rule("01:00", "04:00", weekday=SUNDAY_WEEKDAY),
            duration=minutes(60),
            granularity=minutes(30),
        )

        assert available_starts(slot_inputs) == [
            self.utc("06:00"),
            self.utc("06:30"),
            self.utc("07:00"),
        ]

    def test_a_start_inside_the_gap_resolves_through_the_earlier_offset(self) -> None:
        slot_inputs = self.gap_inputs(rule("02:30", "05:00", weekday=SUNDAY_WEEKDAY))

        first = available_starts(slot_inputs)[0]

        assert first == self.utc("07:30")
        assert first.astimezone(NEW_YORK).strftime("%H:%M %z") == "03:30 -0400"

    def test_an_end_inside_the_gap_resolves_through_the_earlier_offset(self) -> None:
        slot_inputs = self.gap_inputs(
            rule("01:00", "02:30", weekday=SUNDAY_WEEKDAY), duration=minutes(90)
        )

        assert available_starts(slot_inputs) == [self.utc("06:00")]

    def test_a_window_entirely_inside_the_gap_moves_past_it(self) -> None:
        slot_inputs = self.gap_inputs(
            rule("02:15", "02:45", weekday=SUNDAY_WEEKDAY), duration=minutes(30)
        )

        assert available_starts(slot_inputs) == [self.utc("07:15")]

    @pytest.mark.parametrize(
        ("start", "end"), [("02:30", "03:30"), ("02:30", "03:15")], ids=["empty", "reversed"]
    )
    def test_a_rule_collapsed_by_the_gap_gives_no_slots(self, start: str, end: str) -> None:
        slot_inputs = self.gap_inputs(
            rule(start, end, weekday=SUNDAY_WEEKDAY), duration=minutes(1), granularity=minutes(1)
        )

        assert available_starts(slot_inputs) == []

    def test_a_collapsed_rule_does_not_disturb_other_rules(self) -> None:
        slot_inputs = self.gap_inputs(
            rule("02:30", "03:15", weekday=SUNDAY_WEEKDAY),
            rule("06:00", "06:50", weekday=SUNDAY_WEEKDAY),
        )

        assert available_starts(slot_inputs) == [self.utc("10:00")]

    def test_rules_touching_on_the_local_clock_merge_across_the_gap(self) -> None:
        slot_inputs = self.gap_inputs(
            rule("01:00", "03:00", weekday=SUNDAY_WEEKDAY),
            rule("03:00", "04:00", weekday=SUNDAY_WEEKDAY),
            duration=minutes(120),
        )

        assert available_starts(slot_inputs) == [self.utc("06:00")]


class TestDstFold:
    @staticmethod
    def fold_inputs(*rules: WeeklyRule, **overrides: Any) -> SlotInputs:
        return inputs(
            *rules, **({"zone": NEW_YORK, "from_date": DST_FOLD, "to_date": DST_FOLD} | overrides)
        )

    @staticmethod
    def utc(clock_time: str) -> datetime:
        return at(clock_time, DST_FOLD)

    def test_a_window_across_the_fold_is_an_hour_longer(self) -> None:
        slot_inputs = self.fold_inputs(
            rule("00:00", "03:00", weekday=SUNDAY_WEEKDAY),
            duration=minutes(60),
            granularity=minutes(60),
        )

        assert available_starts(slot_inputs) == [
            self.utc("04:00"),
            self.utc("05:00"),
            self.utc("06:00"),
            self.utc("07:00"),
        ]

    def test_the_repeated_hour_gives_two_different_instants(self) -> None:
        slot_inputs = self.fold_inputs(
            rule("00:00", "03:00", weekday=SUNDAY_WEEKDAY),
            duration=minutes(60),
            granularity=minutes(60),
        )

        local = [start.astimezone(NEW_YORK) for start in available_starts(slot_inputs)]

        assert [item.strftime("%H:%M %z") for item in local] == [
            "00:00 -0400",
            "01:00 -0400",
            "01:00 -0500",
            "02:00 -0500",
        ]

    def test_a_rule_inside_the_repeated_hour_uses_its_first_occurrence(self) -> None:
        slot_inputs = self.fold_inputs(
            rule("01:15", "01:45", weekday=SUNDAY_WEEKDAY), duration=minutes(30)
        )

        assert available_starts(slot_inputs) == [self.utc("05:15")]

    def test_an_end_inside_the_repeated_hour_uses_its_first_occurrence(self) -> None:
        slot_inputs = self.fold_inputs(
            rule("00:00", "01:30", weekday=SUNDAY_WEEKDAY), duration=minutes(90)
        )

        assert available_starts(slot_inputs) == [self.utc("04:00")]

    def test_a_start_after_the_fold_uses_the_later_offset(self) -> None:
        slot_inputs = self.fold_inputs(
            rule("03:00", "03:50", weekday=SUNDAY_WEEKDAY),
        )

        assert available_starts(slot_inputs) == [self.utc("08:00")]


def test_starts_of_two_local_dates_that_land_on_one_instant_appear_once() -> None:
    """Nuuk skips the last hour of a Saturday, so that hour resolves onto Sunday morning."""
    saturday = date(2027, 3, 27)
    sunday = date(2027, 3, 28)
    slot_inputs = inputs(
        rule("23:15", "23:45", weekday=saturday.weekday()),
        rule("00:00", "01:00", weekday=sunday.weekday()),
        zone=NUUK,
        from_date=saturday,
        to_date=sunday,
        duration=minutes(15),
    )

    assert available_starts(slot_inputs) == [
        at("01:00", sunday),
        at("01:15", sunday),
        at("01:30", sunday),
        at("01:45", sunday),
    ]


class TestIsAvailableStart:
    def test_a_slot_is_available(self) -> None:
        assert is_available_start(inputs(rule("09:00", "10:00")), at("09:00")) is True

    @pytest.mark.parametrize(
        "start",
        ["09:05", "08:45", "09:15", "12:00"],
        ids=["off_the_grid", "before_the_window", "session_does_not_fit", "after_the_window"],
    )
    def test_a_start_that_is_not_a_slot_is_not_available(self, start: str) -> None:
        assert is_available_start(inputs(rule("09:00", "10:00")), at(start)) is False

    def test_a_date_outside_the_range_is_not_available(self) -> None:
        slot_inputs = inputs(rule(), rule(weekday=TUESDAY_WEEKDAY))

        assert is_available_start(slot_inputs, at("09:00", TUESDAY)) is False

    def test_time_off_makes_a_start_unavailable(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"), time_off=[between("09:30", "09:40")])

        assert is_available_start(slot_inputs, at("09:00")) is False

    def test_the_buffer_of_an_appointment_makes_a_start_unavailable(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "10:00"), buffer=minutes(15), appointments=[between("10:00", "10:50")]
        )

        assert is_available_start(slot_inputs, at("09:00")) is False

    def test_ignoring_appointments_separates_taken_slots_from_unavailable_ones(self) -> None:
        taken = inputs(rule("09:00", "10:00"), appointments=[between("09:00", "09:50")])

        assert is_available_start(taken, at("09:00")) is False
        assert is_available_start(replace(taken, appointments=()), at("09:00")) is True

    def test_the_booking_limits_apply(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"), not_before=at("09:01"))

        assert is_available_start(slot_inputs, at("09:00")) is False

    def test_the_same_instant_at_another_offset_is_available(self) -> None:
        slot_inputs = inputs(rule("09:00", "10:00"))

        assert is_available_start(slot_inputs, at("09:00").astimezone(ISTANBUL)) is True

    def test_a_start_in_the_repeated_hour_of_a_fold_is_found(self) -> None:
        slot_inputs = inputs(
            rule("00:00", "03:00", weekday=SUNDAY_WEEKDAY),
            zone=NEW_YORK,
            from_date=DST_FOLD,
            to_date=DST_FOLD,
            duration=minutes(60),
            granularity=minutes(60),
        )
        second_one_oclock = datetime(2026, 11, 1, 1, 0, fold=1, tzinfo=NEW_YORK)

        assert is_available_start(slot_inputs, second_one_oclock) is True

    def test_it_agrees_with_the_list_for_every_minute_of_a_day(self) -> None:
        slot_inputs = inputs(
            rule("09:00", "12:00"),
            rule("13:10", "15:00"),
            buffer=minutes(10),
            appointments=[between("10:00", "10:50")],
            time_off=[between("14:00", "14:30")],
        )
        slots = set(available_starts(slot_inputs))

        for offset in range(24 * 60):
            start = at("00:00") + minutes(offset)
            assert is_available_start(slot_inputs, start) is (start in slots)


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        (("09:00", "10:00"), False),
        (("09:30", "10:01"), True),
        (("10:30", "10:40"), True),
        (("09:00", "12:00"), True),
        (("10:59", "12:00"), True),
        (("11:00", "12:00"), False),
    ],
    ids=["ends_at_start", "ends_inside", "inside", "around", "starts_inside", "starts_at_end"],
)
def test_intervals_are_half_open(other: tuple[str, str], expected: bool) -> None:
    interval = between("10:00", "11:00")

    assert interval.overlaps(between(*other)) is expected
    assert between(*other).overlaps(interval) is expected


class TestLocalPeriod:
    def test_it_covers_the_first_and_last_local_days(self) -> None:
        period = local_period(MONDAY, TUESDAY, ISTANBUL)

        assert period.starts_at == datetime(2026, 12, 6, 21, 0, tzinfo=UTC)
        assert period.ends_at == datetime(2026, 12, 8, 20, 59, 59, 999_999, tzinfo=UTC)

    @pytest.mark.parametrize(
        "day",
        [DST_GAP, DST_FOLD],
        ids=["gap", "fold"],
    )
    def test_it_holds_every_window_of_a_dst_day(self, day: date) -> None:
        period = local_period(day, day, NEW_YORK)
        slot_inputs = inputs(
            rule("00:00", "23:59", weekday=SUNDAY_WEEKDAY),
            zone=NEW_YORK,
            from_date=day,
            to_date=day,
            duration=minutes(1),
        )

        starts = available_starts(slot_inputs)

        assert starts
        assert period.starts_at <= starts[0]
        assert starts[-1] + minutes(1) <= period.ends_at

    def test_it_holds_a_window_pushed_past_midnight_by_a_gap(self) -> None:
        saturday = date(2027, 3, 27)
        period = local_period(saturday, saturday, NUUK)
        slot_inputs = inputs(
            rule("23:15", "23:45", weekday=saturday.weekday()),
            zone=NUUK,
            from_date=saturday,
            to_date=saturday,
            duration=minutes(30),
        )

        [start] = available_starts(slot_inputs)

        assert start + minutes(30) <= period.ends_at
