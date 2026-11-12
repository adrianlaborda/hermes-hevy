"""Model-facing tools, driven through the shared RUNTIME against the fake Hevy API. No network."""

from __future__ import annotations

import copy
import inspect
import json
from datetime import timedelta

import httpx
import pytest
from conftest import API_KEY, TODAY, TZ, fixture, paged

from hermes_hevy import summaries, writes
from hermes_hevy.dates import parse_instant
from hermes_hevy.tools import RECORD_KINDS, TOOLS, WRITE_TOOLS, WRITE_NOTICE

HANDLERS = {name: handler for name, _schema, handler, _emoji in TOOLS}
EXPECTED_ORDER = [
    "hevy_workouts", "hevy_workout", "hevy_exercises", "hevy_exercise_progress", "hevy_records",
    "hevy_training_load", "hevy_consistency", "hevy_routines", "hevy_body_measurements",
    "hevy_log_workout", "hevy_update_workout", "hevy_save_routine", "hevy_create_routine_folder",
    "hevy_create_exercise", "hevy_log_body_measurement",
]
BENCH = "79D0BB3A"
UPPER_A_ROUTINE_FOLDER = 1123


def run(name: str, args: dict | None = None) -> dict:
    return json.loads(HANDLERS[name](args or {}))


def local_day(workout: dict):
    return parse_instant(workout["start_time"]).astimezone(TZ).date()


def sent_json(request: httpx.Request) -> dict:
    return json.loads(request.content)


# -- registry and schemas -------------------------------------------------------------------------

def test_tool_registry_order_and_write_set():
    assert [name for name, *_ in TOOLS] == EXPECTED_ORDER
    assert WRITE_TOOLS == frozenset(EXPECTED_ORDER[9:])


@pytest.mark.parametrize("name,schema,handler,emoji", TOOLS, ids=[t[0] for t in TOOLS])
def test_every_tool_schema_is_valid(name, schema, handler, emoji):
    assert schema["name"] == name
    assert len(schema["description"]) > 40
    assert emoji
    params = schema["parameters"]
    assert params["type"] == "object"
    assert set(params["required"]) <= set(params["properties"])
    kinds = [p.kind for p in inspect.signature(handler).parameters.values()]
    assert inspect.Parameter.VAR_KEYWORD in kinds
    json.dumps(schema)  # serialisable for the model


def test_write_descriptions_carry_the_approval_notice():
    for name, schema, _handler, emoji in TOOLS:
        if name in WRITE_TOOLS:
            assert WRITE_NOTICE in schema["description"], name
            assert emoji == "✍️"
        else:
            assert WRITE_NOTICE not in schema["description"], name
            assert emoji == "🏋️"


def test_update_workout_description_warns_about_exercises_and_privacy():
    schema = {name: s for name, s, _h, _e in TOOLS}["hevy_update_workout"]
    assert "replaces the whole list" in schema["description"]
    assert "is_private: true" in schema["description"]


def test_write_schemas_have_nested_exercise_and_set_shapes():
    schemas = {name: s for name, s, _h, _e in TOOLS}
    workout_exercises = schemas["hevy_log_workout"]["parameters"]["properties"]["exercises"]["items"]
    assert set(workout_exercises["properties"]) >= {"exercise", "notes", "superset_id", "sets"}
    sets = workout_exercises["properties"]["sets"]["items"]["properties"]
    assert sets["type"]["enum"] == list(writes.SET_TYPES)
    assert sets["rpe"]["enum"] == list(writes.RPE)
    assert "rep_range" not in sets and "rest_seconds" not in workout_exercises["properties"]
    routine_exercises = schemas["hevy_save_routine"]["parameters"]["properties"]["exercises"]["items"]
    assert "rest_seconds" in routine_exercises["properties"]
    routine_sets = routine_exercises["properties"]["sets"]["items"]["properties"]
    assert "rep_range" in routine_sets and "rpe" not in routine_sets


def test_measurement_schema_has_one_number_per_field():
    props = {n: s for n, s, _h, _e in TOOLS}["hevy_log_body_measurement"]["parameters"]["properties"]
    for field in writes.MEASUREMENT_FIELDS:
        assert props[field]["type"] == "number"
    assert "date" in props and props["clear"]["items"]["enum"] == list(writes.MEASUREMENT_FIELDS)


# -- reads ----------------------------------------------------------------------------------------

def test_workouts_default_range_is_last_30_days(runtime, hevy_data):
    out = run("hevy_workouts")
    start, end = TODAY - timedelta(days=29), TODAY
    assert out["range"] == {"start": start.isoformat(), "end": end.isoformat()}
    expected = [w for w in fixture("workouts") if start <= local_day(w) <= end]
    assert out["count"] == len(expected) > 0
    dates = [w["date"] for w in out["workouts"]]
    assert dates == sorted(dates, reverse=True)
    assert out["totals"]["sessions"] == len(expected)


def test_workouts_query_matches_title_or_exercise(runtime, hevy_data):
    out = run("hevy_workouts", {"days": 60, "query": "bench press (barbell)"})
    expected = [w for w in fixture("workouts")
                if any("bench press (barbell)" in e["title"].lower() for e in w["exercises"])
                and local_day(w) >= TODAY - timedelta(days=59)]
    assert out["count"] == len(expected) > 0
    assert out["query"] == "bench press (barbell)"
    by_title = run("hevy_workouts", {"days": 60, "query": "lower a"})
    assert by_title["count"] == sum(1 for w in fixture("workouts") if "lower a" in w["title"].lower()
                                    and local_day(w) >= TODAY - timedelta(days=59))


def test_workouts_limit_and_no_match_note(runtime, hevy_data):
    limited = run("hevy_workouts", {"days": 60, "limit": 2})
    assert len(limited["workouts"]) == 2 and limited["count"] > 2
    empty = run("hevy_workouts", {"days": 60, "query": "zzz no such thing"})
    assert empty["count"] == 0 and "note" in empty


def test_workouts_range_over_366_days_is_an_error(runtime, hevy_data):
    out = run("hevy_workouts", {"days": 400})
    assert "error" in out and "366" in out["error"]


def test_workout_latest_and_detail(runtime, hevy_data):
    newest = max(fixture("workouts"), key=lambda w: parse_instant(w["start_time"]))
    out = run("hevy_workout", {"id": "latest"})
    assert out["id"] == newest["id"]
    assert out["exercises"] and "sets" in out["exercises"][0]


def test_workout_unknown_id_is_an_error(runtime, hevy_data):
    out = run("hevy_workout", {"id": "does-not-exist"})
    assert "error" in out and "hevy_workouts" in out["error"]


def test_exercises_query_ranking_and_counts(runtime, hevy_data):
    out = run("hevy_exercises", {"query": "bench press"})
    rows = out["exercises"]
    assert rows and all("bench press" in r["title"].lower() for r in rows)
    counts = [r["sessions_90d"] for r in rows]
    assert counts == sorted(counts, reverse=True)
    bench = next(r for r in rows if r["id"] == BENCH)
    recent = [w for w in fixture("workouts") if local_day(w) >= TODAY - timedelta(days=89)
              and any(e["exercise_template_id"] == BENCH for e in w["exercises"])]
    assert bench["sessions_90d"] == len(recent) > 0
    assert bench["last_done"] == max(local_day(w) for w in recent).isoformat()

    exact = run("hevy_exercises", {"query": "Bench Press (Dumbbell)"})
    assert exact["exercises"][0]["title"] == "Bench Press (Dumbbell)"


def test_exercises_muscle_equipment_and_custom_filters(runtime, hevy_data):
    chest = run("hevy_exercises", {"muscle": "chest", "limit": 100})["exercises"]
    assert chest and all(r["primary_muscle"] == "chest" or "chest" in r["secondary_muscles"] for r in chest)
    assert any(r["id"] == BENCH for r in chest)
    assert not any(r["title"].startswith("Squat") for r in chest)
    barbell = run("hevy_exercises", {"equipment": "barbell", "limit": 100})["exercises"]
    assert barbell and all(r["equipment"] == "barbell" for r in barbell)
    custom = run("hevy_exercises", {"custom_only": True})["exercises"]
    assert [r["title"] for r in custom] == ["Landmine Press"]
    bad = run("hevy_exercises", {"muscle": "elbows"})
    assert "error" in bad


def test_exercise_progress_for_barbell_bench(runtime, hevy_data):
    out = run("hevy_exercise_progress", {"exercise": "Bench Press (Barbell)"})
    assert out["exercise"]["id"] == BENCH
    assert out["summary"]["best_e1rm"]["date"] == "2026-11-02"
    assert out["summary"]["best_e1rm"]["kg"] == 107.9  # 92.5 kg x 5 by Epley
    assert out["summary"]["best_weight"]["kg"] == 102.5
    assert out["summary"]["best_weight"]["date"] == "2026-11-02"
    assert out["summary"]["prs"]
    assert out["sessions"][0]["date"] == "2026-11-05"  # newest first
    assert len(out["sessions"]) <= 15


def test_exercise_progress_ambiguous_title_lists_candidates(runtime, hevy_data):
    out = run("hevy_exercise_progress", {"exercise": "bench press"})
    assert "error" in out
    assert "Bench Press (Barbell)" in out["error"] and "Bench Press (Dumbbell)" in out["error"]


def test_exercise_progress_requires_exercise(runtime, hevy_data):
    assert "error" in run("hevy_exercise_progress", {})


def test_records_lists_recent_prs_newest_first(runtime, hevy_data):
    out = run("hevy_records", {"days": 30})
    assert out["since"] == (TODAY - timedelta(days=29)).isoformat()
    records = out["records"]
    assert records and len(records) <= 40
    assert all(r["date"] >= out["since"] for r in records)
    assert [r["date"] for r in records] == sorted((r["date"] for r in records), reverse=True)
    assert any(r["exercise_template_id"] == BENCH and r["date"] == "2026-11-02" for r in records)


def test_training_load_returns_expected_keys(runtime, hevy_data):
    out = run("hevy_training_load", {"days": 7})
    assert {"window", "sessions", "working_sets", "volume_kg", "duration_min", "baseline",
            "by_muscle", "push_pull", "flags"} <= set(out)
    assert out["window"]["end"] == TODAY.isoformat()
    assert "error" in run("hevy_training_load", {"days": 40})


def test_consistency_returns_expected_keys(runtime, hevy_data):
    out = run("hevy_consistency", {"weeks": 8})
    assert {"weeks", "avg_sessions_per_week", "current_streak_weeks", "longest_gap_days",
            "last_workout_date", "days_since_last_workout", "usual_days"} <= set(out)
    assert len(out["weeks"]) == 8
    assert "error" in run("hevy_consistency", {"weeks": 1})


def test_routines_list_and_by_id(runtime, hevy_data):
    listing = run("hevy_routines")
    routines = fixture("routines")
    assert listing["folders"] == [{"id": UPPER_A_ROUTINE_FOLDER, "title": "Upper/Lower"}]
    assert len(listing["routines"]) == len(routines)
    upper = next(r for r in listing["routines"] if r["title"] == "Upper A")
    assert upper["folder"] == "Upper/Lower"
    assert upper["exercises"] == len(next(r for r in routines if r["title"] == "Upper A")["exercises"])

    target = routines[0]
    detail = run("hevy_routines", {"id": target["id"]})["routine"]
    assert detail["title"] == target["title"]
    assert detail["exercises"][0]["title"] == target["exercises"][0]["title"]
    assert detail["exercises"][0]["rest_seconds"] == target["exercises"][0]["rest_seconds"]
    assert "error" in run("hevy_routines", {"id": "missing-routine"})


def test_body_measurements_latest(runtime, hevy_data):
    out = run("hevy_body_measurements")
    measurements = fixture("body_measurements")
    newest = max(m["date"] for m in measurements)
    assert out["latest"]["date"] == newest == "2026-11-02"
    assert out["latest"]["weight_kg"] == 80.9
    assert out["measurements"][0] == out["latest"]
    assert all(v is not None for m in out["measurements"] for v in m.values())


def test_raw_flag_adds_hevy_objects(runtime, hevy_data):
    out = run("hevy_workouts", {"days": 60, "raw": True, "limit": 3})
    assert len(out["raw"]) == 3 and out["raw"][0]["id"] == out["workouts"][0]["id"]
    assert "raw" not in run("hevy_workouts", {"days": 60})


# -- writes ---------------------------------------------------------------------------------------

LOG_ARGS = {
    "title": "Leg day",
    "start_time": "2026-11-09 18:00",
    "duration_min": 65,
    "exercises": [{"exercise": "Squat (Barbell)", "sets": [{"weight_kg": 100, "reps": 5}]}],
}


def test_log_workout_posts_exact_body_and_stores_result(runtime, hevy_data, fake):
    created = {
        "id": "NEW-1", "title": "Leg day", "routine_id": None, "description": None,
        "start_time": "2026-11-09T17:00:00Z", "end_time": "2026-11-09T18:05:00Z",
        "exercises": [{"exercise_template_id": "D04AC939", "title": "Squat (Barbell)", "sets": []}],
    }
    fake.routes[("POST", "/v1/workouts")] = httpx.Response(201, json={"workout": [created]})
    args = {**LOG_ARGS, "exercises": [{"exercise": "Squat (Barbell)", "sets": [{"weight_kg": 100, "reps": 5}]}]}
    out = run("hevy_log_workout", args)
    assert out["ok"] is True and out["workout"]["id"] == "NEW-1"

    request = fake.calls("/v1/workouts", "POST")[0]
    assert sent_json(request) == {"workout": {
        "title": "Leg day",
        "description": None,
        "start_time": "2026-11-09T17:00:00Z",
        "end_time": "2026-11-09T18:05:00Z",
        "is_private": False,
        "exercises": [{
            "exercise_template_id": "D04AC939",
            "superset_id": None,
            "notes": None,
            "sets": [{"type": "normal", "weight_kg": 100, "reps": 5, "distance_meters": None,
                      "duration_seconds": None, "custom_metric": None, "rpe": None}],
        }],
    }}
    assert fake.calls("/v1/workouts/NEW-1") == []
    assert runtime.store.workout(runtime.client(), "NEW-1")["title"] == "Leg day"


def test_log_workout_with_ambiguous_exercise_posts_nothing(runtime, hevy_data, fake):
    out = run("hevy_log_workout", {**LOG_ARGS, "exercises": [{"exercise": "bench press", "sets": [{"reps": 5}]}]})
    assert "error" in out
    assert fake.calls("/v1/workouts", "POST") == []


def test_update_workout_title_keeps_exercises(runtime, hevy_data, fake):
    runtime.store.sync(runtime.client())  # the update reads the cached workout
    original = next(w for w in fixture("workouts") if w["exercises"])
    wid = original["id"]

    def echo(request: httpx.Request) -> dict:
        return {"id": wid, **sent_json(request)["workout"]}

    fake.routes[("PUT", f"/v1/workouts/{wid}")] = echo
    out = run("hevy_update_workout", {"id": wid, "title": "Renamed session", "is_private": False})
    assert out["ok"] is True and out["workout"]["title"] == "Renamed session"

    request = fake.calls(f"/v1/workouts/{wid}", "PUT")[0]
    body = sent_json(request)["workout"]
    assert body["title"] == "Renamed session"
    assert body["exercises"] == writes.workout_to_request(original)["workout"]["exercises"]
    assert body["start_time"] == original["start_time"]
    assert body["is_private"] is False


def test_update_workout_is_private_can_be_kept(runtime, hevy_data, fake):
    runtime.store.sync(runtime.client())
    wid = fixture("workouts")[0]["id"]
    fake.routes[("PUT", f"/v1/workouts/{wid}")] = lambda req: {"id": wid, **sent_json(req)["workout"]}
    run("hevy_update_workout", {"id": wid, "is_private": True})
    assert sent_json(fake.calls(f"/v1/workouts/{wid}", "PUT")[0])["workout"]["is_private"] is True


def test_update_unknown_workout_is_an_error(runtime, hevy_data, fake):
    out = run("hevy_update_workout", {"id": "nope", "title": "x"})
    assert "error" in out
    assert fake.calls("/v1/workouts/nope", "PUT") == []


def test_save_routine_creates_with_post(runtime, hevy_data, fake):
    def created(request: httpx.Request) -> httpx.Response:
        routine = sent_json(request)["routine"]
        return httpx.Response(201, json={"routine": {"id": "R-NEW", **routine}})

    fake.routes[("POST", "/v1/routines")] = created
    out = run("hevy_save_routine", {"title": "Upper C", "exercises": [{
        "exercise": "Bench Press (Barbell)", "rest_seconds": 120,
        "sets": [{"reps": 8}, {"rep_range": {"start": 8, "end": 12}, "weight_kg": 60}]}]})
    assert out == {"ok": True, "action": "created",
                   "routine": {"id": "R-NEW", "title": "Upper C", "exercises": 1}}
    body = sent_json(fake.calls("/v1/routines", "POST")[0])["routine"]
    assert body["title"] == "Upper C"
    assert body["exercises"][0]["exercise_template_id"] == BENCH
    assert body["exercises"][0]["rest_seconds"] == 120
    assert body["exercises"][0]["sets"][1]["rep_range"] == {"start": 8, "end": 12}


def test_save_routine_update_keeps_folder_unless_given(runtime, hevy_data, fake):
    routine = fixture("routines")[0]
    rid = routine["id"]
    fake.routes[("PUT", f"/v1/routines/{rid}")] = lambda req: {"routine": {"id": rid, **sent_json(req)["routine"]}}
    out = run("hevy_save_routine", {"routine_id": rid, "title": "Upper A v2"})
    assert out["action"] == "updated" and out["routine"]["title"] == "Upper A v2"
    body = sent_json(fake.calls(f"/v1/routines/{rid}", "PUT")[0])["routine"]
    assert "folder_id" not in body
    assert body["title"] == "Upper A v2"
    assert len(body["exercises"]) == len(routine["exercises"])


def test_hevy_limit_message_reaches_the_model(runtime, hevy_data, fake):
    fake.routes[("POST", "/v1/routines")] = httpx.Response(
        403, json={"error": "Routine limit reached for this plan"})
    out = run("hevy_save_routine", {"title": "Upper D", "exercises": [{
        "exercise": BENCH, "sets": [{"reps": 5}]}]})
    assert "error" in out and "Routine limit reached for this plan" in out["error"]


def test_create_routine_folder(runtime, hevy_data, fake):
    fake.routes[("POST", "/v1/routine_folders")] = httpx.Response(
        201, json={"id": 2001, "index": 2, "title": "Strength", "updated_at": "x", "created_at": "x"})
    out = run("hevy_create_routine_folder", {"title": "Strength"})
    assert out == {"ok": True, "folder": {"id": 2001, "title": "Strength"}}
    assert sent_json(fake.calls("/v1/routine_folders", "POST")[0]) == {"routine_folder": {"title": "Strength"}}


def test_create_exercise_refreshes_templates(runtime, hevy_data, fake, clock):
    client = runtime.client()
    runtime.store.templates(client)  # warm the cache
    before = len(fake.calls("/v1/exercise_templates"))
    clock.now += 5  # the refresh is age-based; a fixed clock would count as zero age
    fake.routes[("POST", "/v1/exercise_templates")] = httpx.Response(200, json={"id": 987654})
    out = run("hevy_create_exercise", {"title": "Cable Fly Plus", "exercise_type": "weight_reps",
                                       "equipment_category": "machine", "muscle_group": "chest",
                                       "other_muscles": ["triceps"]})
    assert out == {"ok": True, "exercise_template_id": "987654", "title": "Cable Fly Plus"}
    assert sent_json(fake.calls("/v1/exercise_templates", "POST")[0]) == {"exercise": {
        "title": "Cable Fly Plus", "exercise_type": "weight_reps", "equipment_category": "machine",
        "muscle_group": "chest", "other_muscles": ["triceps"]}}
    assert len(fake.calls("/v1/exercise_templates")) == before + 1


def test_log_body_measurement_posts_when_day_is_missing(runtime, hevy_data, fake):
    fake.routes[("POST", "/v1/body_measurements")] = httpx.Response(201, json={})
    out = run("hevy_log_body_measurement", {"date": "2026-11-09", "weight_kg": 80.2})
    assert out == {"ok": True, "date": "2026-11-09", "changed": {"weight_kg": 80.2}}
    body = sent_json(fake.calls("/v1/body_measurements", "POST")[0])
    assert body["date"] == "2026-11-09" and body["weight_kg"] == 80.2
    assert body["fat_percent"] is None and "waist" in body


def test_log_body_measurement_puts_merged_values(runtime, hevy_data, fake):
    existing = next(m for m in fixture("body_measurements") if m["date"] == "2026-11-02")
    fake.routes["/v1/body_measurements/2026-11-02"] = existing
    fake.routes[("PUT", "/v1/body_measurements/2026-11-02")] = httpx.Response(200, json={})
    out = run("hevy_log_body_measurement", {"date": "2026-11-02", "weight_kg": 79.8})
    assert out["changed"] == {"weight_kg": 79.8}
    body = sent_json(fake.calls("/v1/body_measurements/2026-11-02", "PUT")[0])
    assert body["weight_kg"] == 79.8
    assert body["fat_percent"] == existing["fat_percent"] == 17.5
    assert "date" not in body
    assert fake.calls("/v1/body_measurements", "POST") == []


def test_update_workout_without_privacy_asks_instead_of_guessing(runtime, hevy_data, fake):
    """Hevy's GET omits is_private and a PUT overwrites it: a private workout must not turn public."""
    runtime.store.sync(runtime.client())
    wid = fixture("workouts")[0]["id"]
    out = run("hevy_update_workout", {"id": wid, "title": "Renamed session"})
    assert "is_private" in out["error"] and fake.calls(f"/v1/workouts/{wid}", "PUT") == []


def test_log_body_measurement_ignores_nulls_and_clears_only_listed_fields(runtime, hevy_data, fake):
    existing = next(m for m in fixture("body_measurements") if m["date"] == "2026-11-02")
    fake.routes["/v1/body_measurements/2026-11-02"] = existing
    fake.routes[("PUT", "/v1/body_measurements/2026-11-02")] = httpx.Response(200, json={})
    nulls = {field: None for field in writes.MEASUREMENT_FIELDS}
    out = run("hevy_log_body_measurement", {**nulls, "date": "2026-11-02", "weight_kg": 79.8, "clear": ["waist"]})
    assert out["changed"] == {"weight_kg": 79.8, "waist": None}
    body = sent_json(fake.calls("/v1/body_measurements/2026-11-02", "PUT")[0])
    assert body["fat_percent"] == existing["fat_percent"] and body["waist"] is None
    assert "error" in run("hevy_log_body_measurement", {"date": "2026-11-02", "clear": ["shoe_size"]})


def test_log_body_measurement_needs_a_field(runtime, hevy_data):
    assert "error" in run("hevy_log_body_measurement", {"date": "2026-11-09"})
    assert "error" in run("hevy_log_body_measurement", {"weight_stone": 12})


# -- errors never raise ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,args", [
    ("hevy_workouts", {"days": "many"}),
    ("hevy_workout", {}),
    ("hevy_exercises", {"muscle": "elbows"}),
    ("hevy_exercise_progress", {}),
    ("hevy_records", {"days": 0}),
    ("hevy_training_load", {"days": 99}),
    ("hevy_consistency", {"weeks": 100}),
    ("hevy_routines", {"id": "missing-routine"}),
    ("hevy_log_workout", {"title": "x"}),
    ("hevy_update_workout", {}),
    ("hevy_save_routine", {"exercises": []}),
    ("hevy_create_routine_folder", {}),
    ("hevy_create_exercise", {"title": "x"}),
    ("hevy_log_body_measurement", {}),
])
def test_bad_arguments_return_an_error_json(runtime, hevy_data, name, args):
    out = run(name, args)
    assert isinstance(out, dict) and "error" in out


def test_server_error_becomes_an_error_json(runtime, fake):
    fake.routes["/v1/workouts"] = httpx.Response(500, json={"error": "boom"})
    out = run("hevy_workouts", {})
    assert "error" in out and "server error" in out["error"]


def test_key_is_never_echoed_in_errors(runtime, fake):
    fake.routes["/v1/workouts"] = httpx.Response(401, json={"error": f"bad key {API_KEY}"})
    out = run("hevy_workouts", {})
    assert "error" in out and API_KEY not in out["error"]


# -- names the user's own workouts use (localized titles) -----------------------------------------

SPANISH = "Press de Banca (Barra)"


def _spanish_workouts(fake):
    """The fixture workouts, with the bench press logged under the user's Spanish app title."""
    workouts = copy.deepcopy(fixture("workouts"))
    for workout in workouts:
        for exercise in workout["exercises"]:
            if exercise["exercise_template_id"] == BENCH:
                exercise["title"] = SPANISH
    fake.routes["/v1/workouts"] = paged("workouts", workouts)
    return workouts


def test_exercises_query_matches_workout_title_and_shows_your_name(runtime, hevy_data, fake):
    _spanish_workouts(fake)
    out = run("hevy_exercises", {"query": "press de banca"})
    assert [r["id"] for r in out["exercises"]] == [BENCH]
    assert out["exercises"][0]["your_name"] == SPANISH
    exact = run("hevy_exercises", {"query": "press de banca (barra)"})
    assert exact["exercises"][0]["id"] == BENCH and exact["exercises"][0]["your_name"] == SPANISH


def test_exercises_english_match_also_carries_your_name_and_others_do_not(runtime, hevy_data, fake):
    _spanish_workouts(fake)
    rows = run("hevy_exercises", {"query": "bench press", "limit": 100})["exercises"]
    bench = next(r for r in rows if r["id"] == BENCH)
    assert bench["your_name"] == SPANISH
    dumbbell = next(r for r in rows if r["title"] == "Bench Press (Dumbbell)")
    assert "your_name" not in dumbbell


def test_exercise_progress_accepts_the_workout_title(runtime, hevy_data, fake):
    _spanish_workouts(fake)
    out = run("hevy_exercise_progress", {"exercise": "press de banca (barra)"})
    assert out["exercise"]["id"] == BENCH and out["exercise"]["your_name"] == SPANISH
    assert out["summary"]["best_e1rm"]["date"] == "2026-11-02"


def test_log_workout_with_workout_title_posts_the_template_id(runtime, hevy_data, fake):
    _spanish_workouts(fake)
    created = {
        "id": "NEW-2", "title": "Leg day", "routine_id": None, "description": None,
        "start_time": "2026-11-09T17:00:00Z", "end_time": "2026-11-09T18:05:00Z",
        "exercises": [{"exercise_template_id": BENCH, "title": SPANISH, "sets": []}],
    }
    fake.routes[("POST", "/v1/workouts")] = httpx.Response(201, json={"workout": [created]})
    out = run("hevy_log_workout", {**LOG_ARGS, "exercises": [{
        "exercise": SPANISH, "sets": [{"weight_kg": 80, "reps": 5}]}]})
    assert out["ok"] is True
    body = sent_json(fake.calls("/v1/workouts", "POST")[0])["workout"]
    assert body["exercises"][0]["exercise_template_id"] == BENCH


def test_records_default_kinds_exclude_volume_and_volume_is_opt_in(runtime, hevy_data):
    default = run("hevy_records", {"days": 60})
    assert default["records"]
    assert not any(r["kind"] == "volume" for r in default["records"])
    volume = run("hevy_records", {"days": 60, "kinds": ["volume"]})
    assert volume["records"] and all(r["kind"] == "volume" for r in volume["records"])
    assert "error" in run("hevy_records", {"kinds": ["bogus"]})
    assert "error" in run("hevy_records", {"kinds": []})


def test_records_total_truncation_and_ordering(runtime, hevy_data):
    everything = run("hevy_records", {"days": 366, "kinds": list(RECORD_KINDS)})
    templates = {t["id"]: t for t in fixture("exercise_templates")}
    expected = summaries.personal_records(fixture("workouts"), TZ, since=TODAY - timedelta(days=365),
                                          templates=templates)
    assert everything["total"] == len(expected)
    if len(expected) > 40:
        assert everything["truncated"] is True and len(everything["records"]) == 40
    else:
        assert "truncated" not in everything
    rank = {kind: i for i, kind in enumerate(RECORD_KINDS)}
    keys = [(r["date"], -rank[r["kind"]]) for r in everything["records"]]
    assert keys == sorted(keys, reverse=True)
