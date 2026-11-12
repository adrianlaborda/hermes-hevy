"""Muscle balance, weekly load and consistency: pure functions, no network."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from conftest import TODAY, TZ
from hermes_hevy.dates import iso_utc
from hermes_hevy.training import consistency, sets_by_muscle, training_load

TEMPLATES = {
    "bench": {"id": "bench", "title": "Bench Press", "primary_muscle_group": "chest",
              "secondary_muscle_groups": ["triceps", "shoulders"]},
    "row": {"id": "row", "title": "Barbell Row", "primary_muscle_group": "lats",
            "secondary_muscle_groups": ["biceps", "upper_back"]},
    "squat": {"id": "squat", "title": "Squat", "primary_muscle_group": "quadriceps",
              "secondary_muscle_groups": ["glutes"]},
}


def tpl_set(index: int = 0, kind: str = "normal", weight: float = 60.0, reps: int = 5) -> dict:
    return {"index": index, "type": kind, "weight_kg": weight, "reps": reps, "distance_meters": None,
            "duration_seconds": None, "rpe": None, "custom_metric": None}


def ex(template_id: str, n: int, weight: float = 60.0, reps: int = 5, kind: str = "normal") -> dict:
    return {"index": 0, "title": template_id, "notes": "", "exercise_template_id": template_id,
            "superset_id": None, "sets": [tpl_set(i, kind, weight, reps) for i in range(n)]}


def wk(day: date, *exercises: dict, hour: int = 18, minutes: int = 60, wid: str = "") -> dict:
    start = datetime.combine(day, time(hour), tzinfo=TZ)
    return {"id": wid or f"w-{day.isoformat()}-{hour}", "title": "Session", "routine_id": None,
            "description": "", "start_time": iso_utc(start),
            "end_time": iso_utc(start + timedelta(minutes=minutes)), "updated_at": "", "created_at": "",
            "exercises": list(exercises)}


def load(*, window=(), base=(), today: date = TODAY, **kw) -> dict:
    """A baseline workout on 2026-10-20 and a window workout on 2026-11-05, each with the given exercises."""
    workouts = []
    if base:
        workouts.append(wk(date(2026, 10, 20), *base))
    if window:
        workouts.append(wk(date(2026, 11, 5), *window))
    return training_load(workouts, TEMPLATES, TZ, today=today, **kw)


def kinds(result: dict) -> set[str]:
    return {f["kind"] for f in result["flags"]}


def flag(result: dict, kind: str) -> dict:
    return next(f for f in result["flags"] if f["kind"] == kind)


# -- sets_by_muscle ----------------------------------------------------------------------------

def test_sets_by_muscle_weights_secondary_half_and_counts_unknown_as_other():
    workouts = [wk(date(2026, 11, 2), ex("bench", 2), ex("row", 2), ex("mystery", 1))]
    assert sets_by_muscle(workouts, TEMPLATES) == {
        "chest": 2.0, "lats": 2.0, "triceps": 1.0, "shoulders": 1.0, "biceps": 1.0, "upper_back": 1.0,
        "other": 1.0}


def test_sets_by_muscle_ignores_warmups_and_respects_secondary_weight():
    workouts = [wk(date(2026, 11, 2), ex("bench", 2), ex("bench", 3, kind="warmup"))]
    assert sets_by_muscle(workouts, TEMPLATES) == {"chest": 2.0, "triceps": 1.0, "shoulders": 1.0}
    assert sets_by_muscle(workouts, TEMPLATES, secondary_weight=1.0) == {
        "chest": 2.0, "triceps": 2.0, "shoulders": 2.0}


# -- training_load: windows and baseline -------------------------------------------------------

def test_workout_on_window_start_counts_and_the_day_before_counts_in_baseline():
    workouts = [wk(date(2026, 11, 3), ex("bench", 3)), wk(date(2026, 11, 2), ex("bench", 4))]
    r = training_load(workouts, TEMPLATES, TZ, today=TODAY)
    assert r["window"] == {"start": "2026-11-03", "end": "2026-11-09", "days": 7}
    assert r["sessions"] == 1 and r["working_sets"] == 3
    assert r["baseline"]["start"] == "2026-10-06" and r["baseline"]["end"] == "2026-11-02"
    assert r["baseline"]["working_sets"] == 1.0  # 4 sets in 28 days, a quarter per 7-day window


def test_baseline_starts_exactly_28_days_before_the_window():
    inside = training_load([wk(date(2026, 10, 6), ex("bench", 4))], TEMPLATES, TZ, today=TODAY)
    outside = training_load([wk(date(2026, 10, 5), ex("bench", 4))], TEMPLATES, TZ, today=TODAY)
    assert inside["baseline"]["working_sets"] == 1.0  # 4 sets * 0.25
    assert outside["baseline"]["working_sets"] == 0.0


def test_workouts_after_today_are_ignored():
    r = training_load([wk(date(2026, 11, 10), ex("bench", 5))], TEMPLATES, TZ, today=TODAY)
    assert r["sessions"] == 0 and r["working_sets"] == 0


def test_baseline_scales_to_the_window_length():
    # Four sessions inside both baselines (7-day: 6 Oct to 2 Nov; 14-day: 29 Sep to 26 Oct): 1 per week
    # against a 7-day window, and 2 per 14 days (scale 0.5) against a 14-day window.
    days = [date(2026, 10, 8), date(2026, 10, 15), date(2026, 10, 22), date(2026, 10, 26)]
    workouts = [wk(d, ex("bench", 5)) for d in days]
    week = training_load(workouts, TEMPLATES, TZ, today=TODAY)
    assert week["baseline"]["sessions"] == 1.0
    assert week["baseline"]["working_sets"] == 5.0
    two_weeks = training_load(workouts, TEMPLATES, TZ, today=TODAY, days=14)
    assert two_weeks["window"]["start"] == "2026-10-27"
    assert two_weeks["baseline"] == {"start": "2026-09-29", "end": "2026-10-26", "sessions": 2.0,
                                     "working_sets": 10.0, "volume_kg": 3000.0}


def test_local_day_uses_the_user_zone_not_utc():
    # 23:30 UTC on 2026-11-02 is already 2026-11-03 in Madrid, so it is in the window.
    late = {**wk(date(2026, 11, 3), ex("bench", 2)), "start_time": "2026-11-02T23:30:00Z",
            "end_time": "2026-11-03T00:30:00Z"}
    r = training_load([late], TEMPLATES, TZ, today=TODAY)
    assert r["sessions"] == 1


def test_duration_and_volume_in_the_window():
    r = training_load([wk(date(2026, 11, 4), ex("bench", 2, weight=100, reps=5), minutes=75)],
                      TEMPLATES, TZ, today=TODAY)
    assert r["duration_min"] == 75.0 and r["volume_kg"] == 1000.0


def test_warmups_and_bodyweight_sets_are_not_volume():
    r = training_load([wk(date(2026, 11, 4), ex("bench", 3, kind="warmup"), ex("bench", 1, weight=0))],
                      TEMPLATES, TZ, today=TODAY)
    assert r["working_sets"] == 1 and r["volume_kg"] == 0.0


# -- training_load: flags ----------------------------------------------------------------------

def test_sets_vs_baseline_fires_at_1_3x_and_not_just_under():
    base = (ex("bench", 40),)  # 40 sets in 28 days = 10 per week, the minimum
    up = load(window=(ex("bench", 13, weight=45),), base=base)
    assert "sets_vs_baseline" in kinds(up)
    assert flag(up, "sets_vs_baseline")["direction"] == "up"
    assert "sets_vs_baseline" not in kinds(load(window=(ex("bench", 12, weight=45),), base=base))


def test_sets_vs_baseline_fires_at_0_7x_and_not_just_above():
    base = (ex("bench", 40),)
    down = load(window=(ex("bench", 7, weight=85),), base=base)
    assert flag(down, "sets_vs_baseline")["direction"] == "down"
    assert "sets_vs_baseline" not in kinds(load(window=(ex("bench", 8, weight=85),), base=base))


def test_sets_vs_baseline_needs_ten_sets_per_week_in_the_baseline():
    big = (ex("bench", 50, weight=45),)
    assert "sets_vs_baseline" not in kinds(load(window=big, base=(ex("bench", 36),)))  # 9 per week
    assert "sets_vs_baseline" in kinds(load(window=big, base=(ex("bench", 40),)))


def test_sets_vs_baseline_text_names_the_numbers():
    r = load(window=(ex("bench", 32, weight=45),), base=(ex("bench", 80, weight=45),))
    assert flag(r, "sets_vs_baseline")["text"] == "Working sets 1.6x your 4-week average (32 vs 20 per week)"


def test_volume_vs_baseline_fires_at_1_3x_on_its_own():
    # 10 sets both ways, but 78 kg x 5 is 1.3x the volume of the 60 kg x 5 baseline.
    r = load(window=(ex("bench", 10, weight=78),), base=(ex("bench", 40),))
    assert "volume_vs_baseline" in kinds(r)
    assert "sets_vs_baseline" not in kinds(r)
    assert "volume_vs_baseline" not in kinds(load(window=(ex("bench", 10, weight=77),), base=(ex("bench", 40),)))


def test_volume_vs_baseline_needs_1000_kg_in_the_baseline():
    # 8 sets of 60 kg x 5 over 28 days is 600 kg per week, under the minimum.
    r = load(window=(ex("bench", 40, weight=100),), base=(ex("bench", 8),))
    assert kinds(r).isdisjoint({"volume_vs_baseline", "sets_vs_baseline"})


def test_volume_vs_baseline_fires_down():
    assert flag(load(window=(ex("bench", 7, weight=60),), base=(ex("bench", 40),)),
                "volume_vs_baseline")["direction"] == "down"


def test_muscle_spike_fires_at_1_5x_with_a_three_set_baseline():
    base = (ex("row", 12),)  # lats: 12 sets * 0.25 = 3.0 per week
    spike = load(window=(ex("row", 9),), base=base)
    assert flag(spike, "muscle_spike")["direction"] == "up"
    assert flag(spike, "muscle_spike")["text"].startswith("Lats sets 3.0x your 4-week average")
    # 16 sets in the baseline: lats 4.0 per week. Six sets now is exactly 1.5x; five is 1.25x.
    assert flag(load(window=(ex("row", 6),), base=(ex("row", 16),)), "muscle_spike")
    assert "muscle_spike" not in kinds(load(window=(ex("row", 5),), base=(ex("row", 16),)))


def test_muscle_spike_needs_a_baseline_of_three_sets_per_week():
    # 8 sets * 0.25 = 2.0 lats per week, below the minimum even at 9 sets now.
    assert "muscle_spike" not in kinds(load(window=(ex("row", 9),), base=(ex("row", 8),)))


def test_muscle_missed_fires_at_four_sets_and_not_below():
    window = (ex("squat", 5),)  # a session this week, no chest work
    assert flag(load(window=window, base=(ex("bench", 16),)), "muscle_missed")["text"] == \
        "No chest sets this week (about 4 per week before)"
    assert "muscle_missed" not in kinds(load(window=window, base=(ex("bench", 12),)))  # 3.0 per week


def test_push_pull_imbalance_needs_six_sets_and_a_clear_skew():
    assert "push_pull_imbalance" not in kinds(load(window=(ex("bench", 2),)))  # push 4, pull 0: too few
    skewed = load(window=(ex("bench", 3),))  # push 6, pull 0: one-sided
    assert flag(skewed, "push_pull_imbalance")["direction"] == "imbalance"
    assert skewed["push_pull"] == {"push_sets": 6.0, "pull_sets": 0.0, "ratio": None}


def test_push_pull_imbalance_thresholds_are_strict():
    # push 6 (bench 3: chest 3, triceps 1.5, shoulders 1.5), pull 4 (row 2: lats 2, biceps 1, upper back 1)
    at_high = load(window=(ex("bench", 3), ex("row", 2)))
    assert at_high["push_pull"]["ratio"] == 1.5
    assert "push_pull_imbalance" not in kinds(at_high)
    assert "push_pull_imbalance" in kinds(load(window=(ex("bench", 4), ex("row", 2))))  # 8 / 4 = 2.0
    # push 4 (bench 2), pull 6 (row 3): 0.67 is at the low threshold, and 4/6 = 0.667 is under it.
    assert "push_pull_imbalance" in kinds(load(window=(ex("bench", 2), ex("row", 3))))
    assert "push_pull_imbalance" not in kinds(load(window=(ex("bench", 3), ex("row", 3))))  # 6 / 6


def test_no_sessions_fires_only_when_the_baseline_had_sessions():
    assert flag(load(base=(ex("bench", 10),)), "no_sessions")["text"] == \
        "No sessions this week (you usually train about 0.2 per week)"
    assert "no_sessions" not in kinds(load())
    assert "no_sessions" not in kinds(load(window=(ex("bench", 1),)))


def test_by_muscle_lists_muscles_in_either_period_with_ratios():
    r = load(window=(ex("bench", 8),), base=(ex("row", 12),))
    rows = {row["muscle"]: row for row in r["by_muscle"]}
    assert rows["chest"] == {"muscle": "chest", "sets": 8.0, "baseline_sets": 0.0, "ratio": None}
    assert rows["lats"]["sets"] == 0.0 and rows["lats"]["baseline_sets"] == 3.0 and rows["lats"]["ratio"] == 0.0
    assert [row["muscle"] for row in r["by_muscle"]][0] == "chest"  # sorted by sets, desc


# -- consistency -------------------------------------------------------------------------------

def sessions(*days: date) -> list[dict]:
    return [wk(d, ex("bench", 3)) for d in days]


def test_weeks_start_on_monday_and_cover_the_requested_span():
    r = consistency(sessions(date(2026, 11, 1), date(2026, 11, 2)), TZ, today=TODAY)
    assert len(r["weeks"]) == 12
    assert r["weeks"][0]["week_start"] == "2026-08-24" and r["weeks"][-1]["week_start"] == "2026-11-09"
    by_week = {w["week_start"]: w["sessions"] for w in r["weeks"]}
    assert by_week["2026-10-26"] == 1  # Sunday 1 Nov belongs to the week of Monday 26 Oct
    assert by_week["2026-11-02"] == 1


def test_week_row_totals():
    r = consistency([wk(date(2026, 11, 3), ex("bench", 3), ex("row", 2), minutes=50)], TZ, today=TODAY)
    row = r["weeks"][-2]
    assert row == {"week_start": "2026-11-02", "sessions": 1, "duration_min": 50.0, "working_sets": 5}


def test_average_excludes_the_current_partial_week():
    # Complete weeks hold 3 sessions over 11 weeks; the current week's 2 sessions are left out.
    r = consistency(sessions(date(2026, 10, 19), date(2026, 10, 26), date(2026, 11, 2),
                             date(2026, 11, 9), date(2026, 11, 9)), TZ, today=TODAY)
    assert r["avg_sessions_per_week"] == 0.3  # 3 / 11; including the current week would give 0.4
    assert r["weeks"][-1]["sessions"] == 2


def test_average_is_none_without_a_complete_week():
    assert consistency([], TZ, today=TODAY, weeks=1)["avg_sessions_per_week"] is None


def test_streak_counts_from_the_current_week_when_it_has_a_session():
    r = consistency(sessions(date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 9)), TZ, today=TODAY)
    assert r["current_streak_weeks"] == 3


def test_streak_starts_at_last_week_when_the_current_week_is_empty():
    r = consistency(sessions(date(2026, 10, 26), date(2026, 11, 2)), TZ, today=TODAY)
    assert r["current_streak_weeks"] == 2


def test_streak_is_zero_when_the_last_two_weeks_are_empty():
    assert consistency(sessions(date(2026, 10, 26)), TZ, today=TODAY)["current_streak_weeks"] == 0


def test_streak_is_not_capped_by_the_week_range():
    days = [TODAY - timedelta(weeks=i) for i in range(15)]  # 15 consecutive Mondays
    r = consistency(sessions(*days), TZ, today=TODAY, weeks=12)
    assert r["current_streak_weeks"] == 15


def test_longest_gap_counts_rest_days_between_sessions():
    r = consistency(sessions(date(2026, 10, 5), date(2026, 10, 8), date(2026, 10, 26)), TZ, today=TODAY)
    assert r["longest_gap_days"] == 17  # 8 Oct to 26 Oct: 17 days with no session
    assert consistency(sessions(date(2026, 10, 5)), TZ, today=TODAY)["longest_gap_days"] is None


def test_usual_days_need_forty_percent_of_complete_weeks():
    complete = [date(2026, 11, 2) - timedelta(weeks=i) for i in range(11)]  # Mondays of the 11 complete weeks
    workouts = []
    for monday in complete:
        workouts.append(wk(monday, ex("bench", 3)))  # Monday every week
        workouts.append(wk(monday + timedelta(days=3), ex("bench", 3)))  # Thursday every week
    for monday in complete[:5]:
        workouts.append(wk(monday + timedelta(days=2), ex("bench", 3)))  # Wednesday: 5 of 11 (45%)
    for monday in complete[:4]:
        workouts.append(wk(monday + timedelta(days=5), ex("bench", 3)))  # Saturday: 4 of 11 (36%)
    workouts.append(wk(date(2026, 11, 8), ex("bench", 3)))  # Sunday of the current week: not counted
    r = consistency(workouts, TZ, today=TODAY)
    assert r["usual_days"] == ["Monday", "Wednesday", "Thursday"]


def test_last_workout_date_and_days_since():
    r = consistency(sessions(date(2026, 11, 2), date(2026, 11, 6)), TZ, today=TODAY)
    assert r["last_workout_date"] == "2026-11-06" and r["days_since_last_workout"] == 3
    empty = consistency([], TZ, today=TODAY)
    assert empty["last_workout_date"] is None and empty["days_since_last_workout"] is None
    assert empty["usual_days"] == [] and empty["weeks"][0]["sessions"] == 0
