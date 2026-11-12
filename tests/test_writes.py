"""Payload builders, merges and previews for Hevy writes. Pure functions, no network."""

from __future__ import annotations

import pytest
from conftest import TZ

from hermes_hevy import writes as w
from hermes_hevy.dates import ArgError

TEMPLATES = {
    "D04AC939": {"id": "D04AC939", "title": "Squat (Barbell)", "type": "weight_reps",
                 "primary_muscle_group": "quadriceps", "secondary_muscle_groups": ["glutes"],
                 "equipment": "barbell", "is_custom": False},
    "05293BCA": {"id": "05293BCA", "title": "Bench Press (Barbell)", "type": "weight_reps",
                 "primary_muscle_group": "chest", "secondary_muscle_groups": ["triceps"],
                 "equipment": "barbell", "is_custom": False},
    "AAA111": {"id": "AAA111", "title": "Bench Press (Dumbbell)", "type": "weight_reps",
               "primary_muscle_group": "chest", "secondary_muscle_groups": [],
               "equipment": "dumbbell", "is_custom": False},
    "BBB222": {"id": "BBB222", "title": "Plank", "type": "duration",
               "primary_muscle_group": "abdominals", "secondary_muscle_groups": [],
               "equipment": "none", "is_custom": False},
}


def _workout_args(**overrides):
    args = {
        "title": "Leg day",
        "start_time": "2026-11-09 18:00",
        "duration_min": 65,
        "exercises": [{"exercise": "D04AC939", "sets": [{"weight_kg": 100, "reps": 5},
                                                         {"weight_kg": 100, "reps": 5}]}],
    }
    args.update(overrides)
    return args


def _get_workout():
    """A workout shaped like GET /v1/workouts/{id}."""
    return {
        "id": "w-1", "title": "Leg day", "routine_id": "r-1", "description": "Felt good",
        "start_time": "2026-11-09T17:00:00Z", "end_time": "2026-11-09T18:05:00Z",
        "updated_at": "2026-11-09T18:06:00Z", "created_at": "2026-11-09T17:00:00Z",
        "exercises": [{
            "index": 0, "title": "Squat (Barbell)", "notes": "Deep", "exercise_template_id": "D04AC939",
            "superset_id": None,
            "sets": [
                {"index": 0, "type": "warmup", "weight_kg": 40, "reps": 10, "distance_meters": None,
                 "duration_seconds": None, "rpe": None, "custom_metric": None},
                {"index": 1, "type": "normal", "weight_kg": 100, "reps": 5, "distance_meters": None,
                 "duration_seconds": None, "rpe": 8, "custom_metric": None},
            ],
        }],
    }


def _get_routine():
    """A routine shaped like GET /v1/routines/{id}."""
    return {
        "id": "r-1", "title": "Upper", "folder_id": 42, "notes": "Slow negatives",
        "updated_at": "2026-11-01T00:00:00Z", "created_at": "2026-10-01T00:00:00Z",
        "exercises": [{
            "index": 0, "title": "Bench Press (Barbell)", "rest_seconds": 90, "notes": None,
            "exercise_template_id": "05293BCA", "superset_id": None,
            "sets": [{"index": 0, "type": "normal", "weight_kg": 80, "reps": None,
                      "rep_range": {"start": 8, "end": 12}, "distance_meters": None,
                      "duration_seconds": None, "custom_metric": None}],
        }],
    }


def _keys_deep(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _keys_deep(v, found)
    elif isinstance(value, list):
        for v in value:
            _keys_deep(v, found)
    return found


# ---------------------------------------------------------------- resolve_exercise

def test_resolve_by_id_and_exact_title_case_insensitive():
    assert w.resolve_exercise("05293BCA", TEMPLATES)["title"] == "Bench Press (Barbell)"
    assert w.resolve_exercise("squat (barbell)", TEMPLATES)["id"] == "D04AC939"
    assert w.resolve_exercise("  PLANK ", TEMPLATES)["id"] == "BBB222"


def test_resolve_unique_substring():
    assert w.resolve_exercise("dumbbell", TEMPLATES)["id"] == "AAA111"
    assert w.resolve_exercise("squat", TEMPLATES)["id"] == "D04AC939"


def test_resolve_ambiguous_substring_lists_candidates():
    with pytest.raises(ArgError) as err:
        w.resolve_exercise("bench press", TEMPLATES)
    message = str(err.value)
    assert "ambiguous" in message
    assert "Bench Press (Barbell) (05293BCA)" in message
    assert "Bench Press (Dumbbell) (AAA111)" in message


def test_resolve_no_match_suggests_close_titles():
    with pytest.raises(ArgError) as err:
        w.resolve_exercise("Squt (Barbell)", TEMPLATES)
    assert "Did you mean" in str(err.value) and "Squat (Barbell) (D04AC939)" in str(err.value)


def test_resolve_no_match_without_close_titles():
    with pytest.raises(ArgError, match="Unknown exercise"):
        w.resolve_exercise("zzzz", TEMPLATES)


def test_resolve_empty_reference():
    with pytest.raises(ArgError, match="exercise is required"):
        w.resolve_exercise("  ", TEMPLATES)


# ---------------------------------------------------------------- sets and rpe

def test_weight_lbs_converts_to_kg():
    assert w.normalize_set({"weight_lbs": 225, "reps": 5}, routine=False)["weight_kg"] == 102.06
    assert w.normalize_set({"weight_lbs": 135}, routine=True)["weight_kg"] == 61.23
    assert w.normalize_set({"weight_kg": 100}, routine=False)["weight_kg"] == 100


def test_weight_kg_and_lbs_together_rejected():
    with pytest.raises(ArgError, match="not both"):
        w.normalize_set({"weight_kg": 100, "weight_lbs": 220}, routine=False)


def test_rpe_must_be_in_scale():
    assert w.normalize_set({"reps": 5, "rpe": 7.5}, routine=False)["rpe"] == 7.5
    with pytest.raises(ArgError, match="rpe 8.25 is not allowed"):
        w.normalize_set({"reps": 5, "rpe": 8.25}, routine=False)
    with pytest.raises(ArgError, match="not allowed"):
        w.normalize_set({"reps": 5, "rpe": 5}, routine=False)


def test_rpe_not_allowed_in_routines():
    with pytest.raises(ArgError, match="only allowed on logged workout sets"):
        w.normalize_set({"reps": 5, "rpe": 8}, routine=True)


def test_rep_range_only_for_routines():
    assert w.normalize_set({"rep_range": {"start": 8, "end": 12}}, routine=True)["rep_range"] == {
        "start": 8, "end": 12}
    with pytest.raises(ArgError, match="only allowed in routines"):
        w.normalize_set({"reps": 8, "rep_range": {"start": 8, "end": 12}}, routine=False)


def test_rep_range_must_be_ordered():
    with pytest.raises(ArgError, match="invalid"):
        w.normalize_set({"rep_range": {"start": 12, "end": 8}}, routine=True)


def test_set_type_and_counts_validated():
    with pytest.raises(ArgError, match="set type 'bogus'"):
        w.normalize_set({"type": "bogus", "reps": 5}, routine=False)
    with pytest.raises(ArgError, match="whole number"):
        w.normalize_set({"reps": 5.5}, routine=False)
    with pytest.raises(ArgError, match="must not be negative"):
        w.normalize_set({"reps": -1}, routine=False)


def test_normalize_set_has_every_request_key():
    out = w.normalize_set({"reps": 5}, routine=False)
    assert out == {"type": "normal", "weight_kg": None, "reps": 5, "distance_meters": None,
                   "duration_seconds": None, "custom_metric": None, "rpe": None}


# ---------------------------------------------------------------- build_workout

def test_build_workout_converts_local_time_to_utc_z():
    body = w.build_workout(_workout_args(), TEMPLATES, TZ)
    workout = body["workout"]
    assert workout["start_time"] == "2026-11-09T17:00:00Z"
    assert workout["end_time"] == "2026-11-09T18:05:00Z"
    assert workout["is_private"] is False
    assert workout["description"] is None
    assert workout["exercises"] == [{
        "exercise_template_id": "D04AC939",
        "superset_id": None,
        "notes": None,
        "sets": [{"type": "normal", "weight_kg": 100, "reps": 5, "distance_meters": None,
                  "duration_seconds": None, "custom_metric": None, "rpe": None}] * 2,
    }]


def test_build_workout_with_end_time_and_offset_input():
    body = w.build_workout(_workout_args(duration_min=None, end_time="2026-11-09 19:05"), TEMPLATES, TZ)
    assert body["workout"]["end_time"] == "2026-11-09T18:05:00Z"
    body = w.build_workout(_workout_args(start_time="2026-11-09T18:00:00+01:00"), TEMPLATES, TZ)
    assert body["workout"]["start_time"] == "2026-11-09T17:00:00Z"


@pytest.mark.parametrize("overrides, message", [
    ({"title": ""}, "title must not be empty"),
    ({"title": None}, "title is required"),
    ({"exercises": []}, "exercises must not be empty"),
    ({"exercises": [{"exercise": "Plank", "sets": []}]}, "exercises\\[0\\] sets must not be empty"),
    ({"exercises": [{"exercise": "Nope", "sets": [{"reps": 1}]}]}, "Unknown exercise"),
    ({"duration_min": 65, "end_time": "2026-11-09 19:05"}, "not both"),
    ({"duration_min": None}, "Give end_time or duration_min"),
    ({"duration_min": 0}, "greater than 0"),
    ({"end_time": "2026-11-09 17:00", "duration_min": None}, "after start_time"),
    ({"start_time": "tomorrow-ish"}, "Invalid time"),
])
def test_build_workout_rejects_bad_input(overrides, message):
    args = _workout_args(**overrides)
    if "end_time" not in overrides and "duration_min" not in overrides:
        args["duration_min"] = 65
    with pytest.raises(ArgError, match=message):
        w.build_workout(args, TEMPLATES, TZ)


# ---------------------------------------------------------------- build_routine

def test_build_routine_shape():
    args = {"title": "Upper", "notes": "Slow", "exercises": [{
        "exercise": "Bench Press (Barbell)", "rest_seconds": 90, "notes": None, "superset_id": None,
        "sets": [{"weight_kg": 80, "rep_range": {"start": 8, "end": 12}}, {"type": "warmup", "reps": 10}],
    }]}
    routine = w.build_routine(args, TEMPLATES)["routine"]
    assert routine["title"] == "Upper" and routine["folder_id"] is None and routine["notes"] == "Slow"
    exercise = routine["exercises"][0]
    assert exercise["exercise_template_id"] == "05293BCA"
    assert exercise["rest_seconds"] == 90 and exercise["superset_id"] is None
    assert exercise["sets"][0]["rep_range"] == {"start": 8, "end": 12}
    assert exercise["sets"][1]["type"] == "warmup" and exercise["sets"][1]["rep_range"] is None


def test_build_routine_rejects_rpe_and_missing_exercises():
    with pytest.raises(ArgError, match="rpe"):
        w.build_routine({"title": "U", "exercises": [{"exercise": "Plank", "sets": [{"rpe": 8}]}]}, TEMPLATES)
    with pytest.raises(ArgError, match="exercises is required"):
        w.build_routine({"title": "U"}, TEMPLATES)


# ---------------------------------------------------------------- GET -> request bodies

def test_workout_to_request_drops_ids_indexes_and_titles():
    body = w.workout_to_request(_get_workout())
    assert set(body) == {"workout"}
    keys = _keys_deep(body)
    for dropped in ("id", "index", "routine_id", "updated_at", "created_at", "title_of_exercise"):
        assert dropped not in keys
    workout = body["workout"]
    assert workout["title"] == "Leg day" and workout["description"] == "Felt good"
    assert workout["is_private"] is False
    exercise = workout["exercises"][0]
    assert set(exercise) == {"exercise_template_id", "superset_id", "notes", "sets"}
    assert exercise["exercise_template_id"] == "D04AC939" and exercise["notes"] == "Deep"
    assert exercise["sets"][1] == {"type": "normal", "weight_kg": 100, "reps": 5, "distance_meters": None,
                                   "duration_seconds": None, "custom_metric": None, "rpe": 8}


def test_routine_to_request_drops_folder_id_and_keeps_rep_range():
    body = w.routine_to_request(_get_routine())
    assert set(body) == {"routine"}
    routine = body["routine"]
    assert "folder_id" not in routine and "id" not in _keys_deep(body)
    exercise = routine["exercises"][0]
    assert exercise["exercise_template_id"] == "05293BCA" and exercise["rest_seconds"] == 90
    assert exercise["sets"][0]["rep_range"] == {"start": 8, "end": 12}
    assert "rpe" not in exercise["sets"][0]


# ---------------------------------------------------------------- applying changes

def test_apply_workout_changes_keeps_unspecified_fields():
    current = w.workout_to_request(_get_workout())
    out = w.apply_workout_changes(current, {"title": "Legs"}, TEMPLATES, TZ)["workout"]
    assert out["title"] == "Legs"
    assert out["start_time"] == current["workout"]["start_time"]
    assert out["end_time"] == current["workout"]["end_time"]
    assert out["exercises"] == current["workout"]["exercises"]
    assert out["description"] == "Felt good"


def test_apply_workout_moving_start_keeps_length():
    current = w.workout_to_request(_get_workout())
    out = w.apply_workout_changes(current, {"start_time": "2026-11-09 19:00"}, TEMPLATES, TZ)["workout"]
    assert out["start_time"] == "2026-11-09T18:00:00Z"
    assert out["end_time"] == "2026-11-09T19:05:00Z"


def test_apply_workout_duration_and_clearing_description():
    current = w.workout_to_request(_get_workout())
    out = w.apply_workout_changes(current, {"duration_min": 30, "description": None}, TEMPLATES, TZ)["workout"]
    assert out["end_time"] == "2026-11-09T17:30:00Z"
    assert out["description"] is None


def test_apply_workout_replaces_exercises_when_given():
    current = w.workout_to_request(_get_workout())
    args = {"exercises": [{"exercise": "Plank", "sets": [{"duration_seconds": 60}]}]}
    out = w.apply_workout_changes(current, args, TEMPLATES, TZ)["workout"]
    assert [e["exercise_template_id"] for e in out["exercises"]] == ["BBB222"]
    assert out["exercises"][0]["sets"][0]["duration_seconds"] == 60
    assert out["title"] == "Leg day"


def test_apply_routine_changes_folder_only_when_given():
    current = w.routine_to_request(_get_routine())
    untouched = w.apply_routine_changes(current, {"notes": "New"}, TEMPLATES)["routine"]
    assert "folder_id" not in untouched and untouched["notes"] == "New"
    moved = w.apply_routine_changes(current, {"folder_id": 7}, TEMPLATES)["routine"]
    assert moved["folder_id"] == 7 and moved["title"] == "Upper"
    replaced = w.apply_routine_changes(current, {"exercises": [{"exercise": "Plank", "sets": [{"reps": 3}]}]},
                                       TEMPLATES)["routine"]
    assert [e["exercise_template_id"] for e in replaced["exercises"]] == ["BBB222"]


# ---------------------------------------------------------------- measurements

def test_merge_measurement_keeps_existing_and_has_all_fields():
    existing = {"weight_kg": 80.5, "waist": 80, "date": "2026-11-08", "id": "m-1"}
    merged = w.merge_measurement(existing, {"weight_kg": 79.8})
    assert list(merged) == list(w.MEASUREMENT_FIELDS)
    assert len(merged) == 17
    assert merged["weight_kg"] == 79.8 and merged["waist"] == 80
    assert merged["chest_cm"] is None and "date" not in merged


def test_merge_measurement_explicit_none_clears():
    merged = w.merge_measurement({"waist": 80}, {"waist": None})
    assert merged["waist"] is None


def test_merge_measurement_without_existing():
    merged = w.merge_measurement(None, {"chest_cm": 95})
    assert merged["chest_cm"] == 95
    assert [k for k, v in merged.items() if v is not None] == ["chest_cm"]


def test_merge_measurement_rejects_unknown_and_non_positive():
    with pytest.raises(ArgError, match="Unknown measurement 'bmi'"):
        w.merge_measurement(None, {"bmi": 22})
    with pytest.raises(ArgError, match="greater than 0"):
        w.merge_measurement(None, {"weight_kg": 0})
    with pytest.raises(ArgError, match="greater than 0"):
        w.merge_measurement({"waist": 80}, {"waist": -1})


# ---------------------------------------------------------------- custom exercises

def test_build_exercise_template_valid():
    body = w.build_exercise_template({"title": "Cable Fly", "exercise_type": "weight_reps",
                                      "equipment_category": "machine", "muscle_group": "chest",
                                      "other_muscles": ["triceps", "shoulders"]})
    assert body == {"exercise": {"title": "Cable Fly", "exercise_type": "weight_reps",
                                 "equipment_category": "machine", "muscle_group": "chest",
                                 "other_muscles": ["triceps", "shoulders"]}}


@pytest.mark.parametrize("field, value, allowed", [
    ("exercise_type", "weights", "weight_reps"),
    ("equipment_category", "bench", "barbell"),
    ("muscle_group", "legs", "quadriceps"),
])
def test_build_exercise_template_rejects_bad_enum(field, value, allowed):
    with pytest.raises(ArgError, match=f"{field} {value!r} is not allowed") as err:
        w.build_exercise_template({**VALID_EXERCISE, field: value})
    assert allowed in str(err.value)


VALID_EXERCISE = {"title": "X", "exercise_type": "weight_reps", "equipment_category": "machine",
                  "muscle_group": "chest"}


def test_build_exercise_template_rejects_bad_other_muscle_and_missing_title():
    with pytest.raises(ArgError, match="other_muscles"):
        w.build_exercise_template({**VALID_EXERCISE, "other_muscles": ["legs"]})
    with pytest.raises(ArgError, match="title is required"):
        w.build_exercise_template({**VALID_EXERCISE, "title": None})


@pytest.mark.parametrize("field", ["exercise_type", "equipment_category", "muscle_group"])
def test_build_exercise_template_requires_type_equipment_and_muscle(field):
    with pytest.raises(ArgError, match=f"{field} is required"):
        w.build_exercise_template({k: v for k, v in VALID_EXERCISE.items() if k != field})


def test_enum_lists_match_openapi():
    assert len(w.CUSTOM_EXERCISE_TYPES) == 8 and "short_distance_weight" in w.CUSTOM_EXERCISE_TYPES
    assert len(w.MUSCLE_GROUPS) == 20 and "full_body" in w.MUSCLE_GROUPS
    assert len(w.EQUIPMENT_CATEGORIES) == 9 and "resistance_band" in w.EQUIPMENT_CATEGORIES
    assert w.RPE == (6, 7, 7.5, 8, 8.5, 9, 9.5, 10)
    assert w.SET_TYPES == ("normal", "warmup", "failure", "dropset")


# ---------------------------------------------------------------- previews

def test_describe_workout_key_facts():
    body = w.build_workout(_workout_args(), TEMPLATES, TZ)
    text = w.describe_workout(body, TEMPLATES, TZ)
    lines = text.splitlines()
    assert lines[0].startswith("Log workout “Leg day” on ")
    assert "2026-11-09 18:00–19:05 (65 min)" in lines[0]
    assert "private: no" in lines[0]
    assert lines[1] == "- Squat (Barbell): 100 kg x 5, 100 kg x 5"


def test_describe_workout_action_and_private_and_warmup():
    body = w.build_workout(_workout_args(is_private=True, exercises=[{"exercise": "Plank", "sets": [
        {"type": "warmup", "weight_kg": 40, "reps": 10}, {"reps": 8, "rpe": 8}]}]), TEMPLATES, TZ)
    text = w.describe_workout(body, TEMPLATES, TZ, action="Update")
    assert text.startswith("Update workout")
    assert "private: yes" in text
    assert "- Plank: warmup 40 kg x 10, x 8 (RPE 8)" in text


def test_describe_workout_is_capped_at_1500_chars():
    sets = [{"weight_kg": 100, "reps": 5}] * 300
    body = w.build_workout(_workout_args(exercises=[{"exercise": "Squat", "sets": sets}]), TEMPLATES, TZ)
    text = w.describe_workout(body, TEMPLATES, TZ)
    assert len(text) == 1500 and text.endswith("…")
    short = w.describe_workout(w.build_workout(_workout_args(), TEMPLATES, TZ), TEMPLATES, TZ)
    assert not short.endswith("…")


def test_describe_routine_lists_exercises_and_ranges():
    body = w.build_routine({"title": "Upper", "folder_id": 7, "exercises": [{
        "exercise": "Bench Press (Barbell)", "rest_seconds": 90,
        "sets": [{"weight_kg": 80, "rep_range": {"start": 8, "end": 12}}]}]}, TEMPLATES)
    text = w.describe_routine(body, TEMPLATES)
    assert text.splitlines()[0] == "Create routine “Upper” (1 exercise, folder 7)"
    assert text.splitlines()[1] == "- Bench Press (Barbell): 80 kg x 8-12, rest 90 s"
    assert w.describe_routine(w.routine_to_request(_get_routine()), TEMPLATES, action="Update").startswith(
        "Update routine “Upper” (1 exercise)")


def test_describe_routine_truncates():
    sets = [{"weight_kg": 10, "reps": 5}] * 300
    body = w.build_routine({"title": "Long", "exercises": [{"exercise": "Squat", "sets": sets}]}, TEMPLATES)
    text = w.describe_routine(body, TEMPLATES)
    assert len(text) == 1500 and text.endswith("…")


def test_describe_measurement_lists_only_changed_fields():
    existing = {"weight_kg": 80.5, "waist": 80.0, "fat_percent": 18.5}
    body = w.merge_measurement(existing, {"weight_kg": 79.8})
    text = w.describe_measurement("2026-11-09", body, existing)
    assert text.splitlines() == ["Save body measurement for 2026-11-09", "weight_kg 80.5 → 79.8"]


def test_describe_measurement_clear_and_new_values():
    existing = {"waist": 80}
    body = w.merge_measurement(existing, {"waist": None, "chest_cm": 95})
    text = w.describe_measurement("2026-11-09", body, existing)
    assert "waist 80 → –" in text and "chest_cm – → 95" in text
    assert "weight_kg" not in text


def test_describe_measurement_without_changes():
    existing = {"weight_kg": 80.5}
    body = w.merge_measurement(existing, {})
    assert "no changes" in w.describe_measurement("2026-11-09", body, existing)


def test_describe_exercise_template():
    body = w.build_exercise_template({"title": "Cable Fly", "exercise_type": "weight_reps",
                                      "equipment_category": "machine", "muscle_group": "chest",
                                      "other_muscles": ["triceps"]})
    text = w.describe_exercise_template(body)
    assert text == ("Create custom exercise “Cable Fly” (weight_reps, machine, chest, also triceps)")


# ---------------------------------------------------------------- aliases (titles from the user's workouts)

ALIASES = {"Press de Banca (Barra)": "05293BCA", "Curl de Pierna Sentado": "BBB222"}


def test_alias_exact_match_is_case_insensitive_and_reports_matched_name():
    out = w.resolve_exercise("press DE banca (barra)", TEMPLATES, ALIASES)
    assert out["id"] == "05293BCA" and out["title"] == "Bench Press (Barbell)"
    assert out["matched_name"] == "Press de Banca (Barra)"


def test_English_title_match_has_no_matched_name():
    out = w.resolve_exercise("Bench Press (Barbell)", TEMPLATES, ALIASES)
    assert out["id"] == "05293BCA" and "matched_name" not in out


def test_alias_substring_is_unique_and_reports_matched_name():
    out = w.resolve_exercise("banca", TEMPLATES, ALIASES)
    assert out["id"] == "05293BCA" and out["matched_name"] == "Press de Banca (Barra)"


def test_ambiguity_across_english_titles_and_aliases_lists_candidates():
    with pytest.raises(ArgError) as err:
        w.resolve_exercise("press", TEMPLATES, {"Press Plano": "BBB222"})
    message = str(err.value)
    assert "ambiguous" in message
    assert "Bench Press (Barbell) (05293BCA)" in message
    assert "Bench Press (Dumbbell) (AAA111)" in message
    assert "Plank (BBB222) as “Press Plano”" in message
    with pytest.raises(ArgError) as err:
        w.resolve_exercise("press", TEMPLATES, ALIASES)
    assert str(err.value).count("(05293BCA)") == 1  # reached through its title and its alias: listed once


def test_id_reached_through_title_and_alias_counts_once():
    aliases = {"Press (Barbell) de banca": "05293BCA"}
    out = w.resolve_exercise("press (barbell", TEMPLATES, aliases)
    assert out["id"] == "05293BCA" and "matched_name" not in out


def test_alias_pointing_at_unknown_template_is_ignored():
    with pytest.raises(ArgError, match="Unknown exercise"):
        w.resolve_exercise("Fantasma", TEMPLATES, {"Fantasma": "NOPE"})


def test_alias_typo_suggests_the_alias_with_its_english_title():
    with pytest.raises(ArgError) as err:
        w.resolve_exercise("press de banka (barra)", TEMPLATES, ALIASES)
    assert "Did you mean" in str(err.value)
    assert "Bench Press (Barbell) (05293BCA) as “Press de Banca (Barra)”" in str(err.value)


def test_build_workout_resolves_spanish_name_with_aliases_only():
    args = _workout_args(exercises=[{"exercise": "Curl de Pierna Sentado", "sets": [{"reps": 10}]}])
    body = w.build_workout(args, TEMPLATES, TZ, aliases=ALIASES)
    assert body["workout"]["exercises"][0]["exercise_template_id"] == "BBB222"
    with pytest.raises(ArgError, match="Unknown exercise"):
        w.build_workout(args, TEMPLATES, TZ)


def test_routine_and_update_builders_take_aliases():
    routine = w.build_routine({"title": "U", "exercises": [{"exercise": "press de banca (barra)",
                                                            "sets": [{"reps": 5}]}]}, TEMPLATES, aliases=ALIASES)
    assert routine["routine"]["exercises"][0]["exercise_template_id"] == "05293BCA"
    current = w.workout_to_request(_get_workout())
    changed = w.apply_workout_changes(current, {"exercises": [{"exercise": "Curl de Pierna Sentado",
                                                               "sets": [{"reps": 8}]}]},
                                      TEMPLATES, TZ, aliases=ALIASES)
    assert changed["workout"]["exercises"][0]["exercise_template_id"] == "BBB222"
    kept = w.apply_routine_changes(w.routine_to_request(_get_routine()),
                                   {"exercises": [{"exercise": "Curl de Pierna Sentado", "sets": [{"reps": 3}]}]},
                                   TEMPLATES, aliases=ALIASES)
    assert kept["routine"]["exercises"][0]["exercise_template_id"] == "BBB222"
