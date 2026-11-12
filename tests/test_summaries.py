"""Pure summaries over hand-built Hevy workouts: e1RM, volume, dates, PRs, progress and muscles."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pytest
from conftest import TODAY, TZ

from hermes_hevy.summaries import (
    compact_set,
    e1rm,
    exercise_sessions,
    hmm,
    is_working,
    personal_records,
    progress,
    set_label,
    set_volume_kg,
    workout_detail,
    workout_summary,
)

TEMPLATES = {
    "BENCH": {"id": "BENCH", "title": "Bench Press (Barbell)", "type": "weight_reps",
              "primary_muscle_group": "chest", "secondary_muscle_groups": ["triceps"],
              "equipment": "barbell", "is_custom": False},
    "ROW": {"id": "ROW", "title": "Seated Row (Cable)", "type": "weight_reps",
            "primary_muscle_group": "lats", "secondary_muscle_groups": [],
            "equipment": "cable", "is_custom": False},
    "PULLUP": {"id": "PULLUP", "title": "Pull Up", "type": "bodyweight_reps",
               "primary_muscle_group": "lats", "secondary_muscle_groups": ["biceps"],
               "equipment": "none", "is_custom": False},
}


# -- builders ----------------------------------------------------------------------------------

def make_set(weight=None, reps=None, type="normal", *, distance=None, duration=None, rpe=None):
    return {"index": 0, "type": type, "weight_kg": weight, "reps": reps, "distance_meters": distance,
            "duration_seconds": duration, "rpe": rpe, "custom_metric": None}


def make_exercise(template_id, sets, title="", *, notes=None, superset_id=None):
    return {"index": 0, "title": title or TEMPLATES.get(template_id, {}).get("title", template_id),
            "notes": notes, "exercise_template_id": template_id, "superset_id": superset_id, "sets": sets}


def make_workout(id, start_iso, exercises, minutes=60, title="Session", **extra):
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = start + timedelta(minutes=minutes)
    return {"id": id, "title": title, "routine_id": None, "description": "", "start_time": start_iso,
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "updated_at": start_iso, "created_at": start_iso,
            "exercises": exercises, **extra}


def log(template_id, entries):
    """One workout per ``(day, weight, reps)`` entry, each holding one set of ``template_id``."""
    return [
        make_workout(f"w{i}", f"{day}T12:00:00Z", [make_exercise(template_id, [make_set(weight, reps)])])
        for i, (day, weight, reps) in enumerate(entries)
    ]


def sessions_of(template_id, entries):
    return exercise_sessions(log(template_id, entries), template_id, TZ)


# -- e1RM and sets -----------------------------------------------------------------------------

def test_e1rm_one_rep_is_the_weight():
    assert e1rm(100, 1) == 100


def test_e1rm_epley_up_to_twelve_reps():
    assert e1rm(100, 5) == pytest.approx(100 * (1 + 5 / 30))
    assert e1rm(100, 12) == pytest.approx(140)


def test_e1rm_thirteen_reps_has_no_estimate():
    assert e1rm(100, 13) is None


def test_e1rm_zero_or_missing_weight_or_reps_has_no_estimate():
    assert e1rm(0, 5) is None
    assert e1rm(None, 5) is None
    assert e1rm(100, 0) is None
    assert e1rm(100, None) is None


def test_warmups_are_not_working():
    assert [is_working(make_set(50, 5, t)) for t in ("normal", "failure", "dropset", "warmup")] == [
        True, True, True, False]


def test_volume_counts_working_sets_only():
    assert set_volume_kg(make_set(100, 5)) == 500
    assert set_volume_kg(make_set(40, 10, "warmup")) == 0
    assert set_volume_kg(make_set(0, 10)) == 0
    assert set_volume_kg(make_set(None, 10)) == 0


def test_summary_excludes_warmups_from_sets_reps_and_volume():
    w = make_workout("w1", "2026-11-06T17:00:00Z", [
        make_exercise("BENCH", [make_set(60, 10, "warmup"), make_set(100, 5), make_set(100, 5, "failure")]),
    ])
    s = workout_summary(w, TZ)
    assert s["working_sets"] == 2
    assert s["reps"] == 10
    assert s["volume_kg"] == 1000


def test_compact_set_drops_none_and_normal_type():
    assert compact_set(make_set(100, 5, rpe=8)) == {"weight_kg": 100, "reps": 5, "rpe": 8}
    assert compact_set(make_set(40, 10, "warmup"))["type"] == "warmup"


def test_set_label_formats():
    assert set_label(make_set(100, 5)) == "100 kg x 5"
    assert set_label(make_set(62.5, 8)) == "62.5 kg x 8"
    assert set_label(make_set(100, 5, rpe=8)) == "100 kg x 5 + RPE 8"
    assert set_label(make_set(None, 12)) == "x 12"
    assert set_label(make_set(duration=45)) == "45 s"
    assert set_label(make_set(distance=1200)) == "1.2 km"
    assert set_label(make_set(distance=500)) == "500 m"


def test_hmm():
    assert hmm(3900) == "1:05"
    assert hmm(None) is None


# -- local dates -------------------------------------------------------------------------------

def test_workout_at_2330_utc_is_the_next_local_day_in_madrid():
    w = make_workout("late", "2026-11-08T23:30:00Z", [make_exercise("BENCH", [make_set(100, 5)])])
    s = workout_summary(w, TZ)
    assert s["date"] == "2026-11-09"
    assert s["start_local"] == "00:30"
    assert s["end_local"] == "01:30"
    assert exercise_sessions([w], "BENCH", TZ)[0]["date"] == "2026-11-09"


def test_daytime_workout_keeps_its_date_and_duration():
    w = make_workout("day", "2026-11-09T10:00:00Z", [make_exercise("BENCH", [make_set(100, 5)])], minutes=65)
    s = workout_summary(w, TZ)
    assert s["date"] == "2026-11-09"
    assert s["start_local"] == "11:00"
    assert s["duration_min"] == 65
    assert s["duration_hmm"] == "1:05"


def test_summary_sets_by_muscle_with_templates_and_unknown_template_as_other():
    w = make_workout("m", "2026-11-06T17:00:00Z", [
        make_exercise("BENCH", [make_set(60, 10, "warmup"), make_set(100, 5), make_set(100, 5)]),
        make_exercise("ROW", [make_set(80, 10)]),
        make_exercise("NOPE", [make_set(20, 10)]),
    ])
    assert workout_summary(w, TZ, TEMPLATES)["sets_by_muscle"] == {"chest": 2, "lats": 1, "other": 1}
    assert "sets_by_muscle" not in workout_summary(w, TZ)


def test_detail_has_notes_superset_and_best_set_only_when_set():
    w = make_workout("d", "2026-11-06T17:00:00Z", [
        make_exercise("BENCH", [make_set(100, 5)], notes="pause", superset_id="A"),
        make_exercise("ROW", [make_set(80, 10)]),
    ], description="Push day")
    detail = workout_detail(w, TZ, TEMPLATES)
    bench, row = detail["exercises"]
    assert detail["description"] == "Push day"
    assert bench["notes"] == "pause" and bench["superset_id"] == "A" and bench["muscle"] == "chest"
    assert "notes" not in row and "superset_id" not in row
    assert bench["best_set"] == "100 kg x 5"
    assert bench["best_e1rm_kg"] == round(e1rm(100, 5), 1)


# -- best set ----------------------------------------------------------------------------------

def best_set_of(sets):
    return workout_detail(make_workout("b", "2026-11-06T17:00:00Z", [make_exercise("BENCH", sets)]), TZ)[
        "exercises"][0]["best_set"]


def test_best_set_prefers_highest_e1rm_over_heavier_set():
    # 100 x 1 -> e1RM 100; 90 x 5 -> e1RM 105
    assert best_set_of([make_set(100, 1), make_set(90, 5)]) == "90 kg x 5"


def test_best_set_ignores_heavier_sets_without_e1rm_when_others_have_one():
    # 120 x 13 has no e1RM; 110 x 3 does
    assert best_set_of([make_set(120, 13), make_set(110, 3)]) == "110 kg x 3"


def test_best_set_falls_back_to_heaviest():
    assert best_set_of([make_set(130, 13), make_set(120, 14)]) == "130 kg x 13"


def test_best_set_falls_back_to_most_reps_without_weight():
    assert best_set_of([make_set(None, 10), make_set(None, 12)]) == "x 12"


def test_best_set_never_uses_warmups():
    assert best_set_of([make_set(200, 1, "warmup"), make_set(50, 8)]) == "50 kg x 8"


# -- exercise sessions -------------------------------------------------------------------------

def test_sessions_merge_two_entries_of_one_exercise_in_one_workout():
    w = make_workout("merge", "2026-10-01T17:00:00Z", [
        make_exercise("BENCH", [make_set(100, 5), make_set(40, 10, "warmup")]),
        make_exercise("BENCH", [make_set(110, 3)], superset_id="A"),
    ])
    rows = exercise_sessions([w], "BENCH", TZ)
    assert len(rows) == 1
    row = rows[0]
    assert row["working_sets"] == 2
    assert row["reps"] == 8
    assert row["volume_kg"] == 830
    assert row["top_weight_kg"] == 110
    assert row["top_set"] == "110 kg x 3"
    assert row["best_reps"] == 5
    assert row["best_e1rm_kg"] == pytest.approx(121.0)


def test_sessions_are_oldest_first_and_only_include_the_exercise():
    workouts = log("BENCH", [("2026-10-08", 100, 5), ("2026-10-01", 100, 5)])
    workouts.append(make_workout("row", "2026-10-03T12:00:00Z", [make_exercise("ROW", [make_set(80, 10)])]))
    rows = exercise_sessions(workouts, "BENCH", TZ)
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-08"]


def test_bodyweight_session_has_no_weight_keys():
    rows = exercise_sessions(log("PULLUP", [("2026-10-01", None, 8)]), "PULLUP", TZ)
    assert rows[0]["best_reps"] == 8
    assert "top_weight_kg" not in rows[0] and "best_e1rm_kg" not in rows[0] and "top_set" not in rows[0]


# -- progress ----------------------------------------------------------------------------------

WEEKLY = ["2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29", "2026-10-06"]


def test_progress_slope_per_four_weeks_and_change_pct_with_six_sessions():
    # reps 1 so e1RM == weight; +2 kg a week is +8 kg per four weeks
    sessions = sessions_of("BENCH", [(d, w, 1) for d, w in zip(WEEKLY, [100, 102, 104, 106, 108, 110])])
    trend = progress(sessions, today=TODAY)["e1rm_trend"]
    assert trend["slope_kg_per_4_weeks"] == pytest.approx(8.0)
    assert trend["change_pct"] == pytest.approx(5.9)  # mean(106, 108, 110) = 108 vs mean(100, 102, 104) = 102


def test_progress_change_pct_is_none_below_six_sessions():
    sessions = sessions_of("BENCH", [(d, w, 1) for d, w in zip(WEEKLY, [100, 100, 100, 110, 110])])
    assert progress(sessions, today=TODAY)["e1rm_trend"]["change_pct"] is None


def test_progress_reports_bests_and_days_since_last():
    sessions = sessions_of("BENCH", [("2026-10-01", 100, 1), ("2026-10-08", 110, 1), ("2026-10-15", 105, 1)])
    p = progress(sessions, today=TODAY)
    assert p["sessions"] == 3
    assert p["first_date"] == "2026-10-01" and p["last_date"] == "2026-10-15"
    assert p["days_since_last"] == 25
    assert p["best_e1rm"] == {"kg": 110, "date": "2026-10-08"}
    assert p["best_weight"] == {"kg": 110, "date": "2026-10-08", "set": "110 kg x 1"}


def test_progress_empty_history():
    p = progress([], today=TODAY)
    assert p["sessions"] == 0 and p["last_date"] is None and p["days_since_last"] is None
    assert p["best_e1rm"] is None and p["prs"] == [] and p["stalled"] is False


def stalled_for(entries):
    return progress(sessions_of("BENCH", entries), today=TODAY)["stalled"]


def test_stalled_when_last_four_sessions_did_not_beat_an_old_best():
    assert stalled_for([("2026-09-01", 120, 1), ("2026-09-08", 100, 1), ("2026-09-15", 100, 1),
                        ("2026-09-22", 100, 1), ("2026-09-29", 100, 1)]) is True


def test_not_stalled_when_the_last_four_beat_the_earlier_best():
    assert stalled_for([("2026-09-01", 120, 1), ("2026-09-08", 100, 1), ("2026-09-15", 100, 1),
                        ("2026-09-22", 100, 1), ("2026-09-29", 125, 1)]) is False


def test_not_stalled_when_the_best_is_younger_than_three_weeks():
    assert stalled_for([("2026-10-20", 120, 1), ("2026-10-27", 100, 1), ("2026-11-01", 100, 1),
                        ("2026-11-03", 100, 1), ("2026-11-06", 100, 1)]) is False


def test_not_stalled_with_fewer_than_four_sessions():
    assert stalled_for([("2026-09-01", 120, 1), ("2026-09-08", 100, 1), ("2026-09-15", 100, 1)]) is False


def test_prs_skip_the_first_session_and_only_list_beats():
    sessions = sessions_of("BENCH", [("2026-10-01", 100, 1), ("2026-10-08", 105, 1), ("2026-10-15", 103, 1)])
    prs = progress(sessions, today=TODAY)["prs"]
    assert {(p["date"], p["kind"]) for p in prs} == {("2026-10-08", "e1rm"), ("2026-10-08", "weight")}
    assert all(p["date"] != "2026-10-01" for p in prs)
    assert {p["previous"] for p in prs} == {100}


def test_prs_empty_for_a_single_session():
    assert progress(sessions_of("BENCH", [("2026-10-01", 100, 1)]), today=TODAY)["prs"] == []


def test_progress_prs_use_reps_for_bodyweight_exercises():
    sessions = sessions_of("PULLUP", [("2026-10-01", None, 8), ("2026-10-08", None, 10), ("2026-10-15", None, 9)])
    assert progress(sessions, today=TODAY)["prs"] == [
        {"date": "2026-10-08", "kind": "reps", "value": 10, "previous": 8}]


# -- personal records --------------------------------------------------------------------------

def test_personal_records_filter_by_since_and_sort_newest_first():
    workouts = log("BENCH", [("2026-09-01", 100, 1), ("2026-09-08", 105, 1), ("2026-10-06", 110, 1)])
    rows = personal_records(workouts, TZ, since=date(2026, 9, 5), templates=TEMPLATES)
    # 09-08 beats 09-01 on e1RM, weight and volume; 10-06 beats it again on each kind
    assert [r["date"] for r in rows] == ["2026-10-06"] * 3 + ["2026-09-08"] * 3


def test_personal_records_row_shape_and_kinds():
    workouts = log("BENCH", [("2026-09-01", 100, 1), ("2026-09-08", 105, 1)])
    rows = personal_records(workouts, TZ, since=date(2026, 1, 1), templates=TEMPLATES)
    kinds = {r["kind"] for r in rows}
    assert kinds == {"e1rm", "weight", "volume"}
    weight_row = next(r for r in rows if r["kind"] == "weight")
    assert weight_row == {"date": "2026-09-08", "exercise": "Bench Press (Barbell)", "exercise_template_id": "BENCH",
                          "kind": "weight", "value": 105, "previous": 100, "unit": "kg",
                          "workout_id": "w1"}


def test_personal_records_reps_kind_for_bodyweight_exercise():
    workouts = log("PULLUP", [("2026-10-01", None, 8), ("2026-10-08", None, 10), ("2026-10-15", None, 9)])
    rows = personal_records(workouts, TZ, since=date(2026, 1, 1), templates=TEMPLATES)
    assert len(rows) == 1
    assert rows[0]["kind"] == "reps" and rows[0]["value"] == 10 and rows[0]["previous"] == 8
    assert rows[0]["unit"] == "reps" and rows[0]["date"] == "2026-10-08"


def test_personal_records_exclude_first_ever_session():
    workouts = log("BENCH", [("2026-10-01", 100, 5)]) + log("PULLUP", [("2026-10-01", None, 8)])
    assert personal_records(workouts, TZ, since=date(2026, 1, 1), templates=TEMPLATES) == []


def test_personal_records_do_not_depend_on_input_order():
    entries = [("2026-09-01", 100, 1), ("2026-09-08", 105, 1), ("2026-09-15", 103, 1), ("2026-09-22", 110, 1)]
    workouts = log("BENCH", entries)
    shuffled = list(workouts)
    random.Random(7).shuffle(shuffled)
    expected = personal_records(workouts, TZ, since=date(2026, 1, 1), templates=TEMPLATES)
    assert personal_records(shuffled, TZ, since=date(2026, 1, 1), templates=TEMPLATES) == expected
    assert {r["value"] for r in expected if r["kind"] == "weight"} == {105, 110}
