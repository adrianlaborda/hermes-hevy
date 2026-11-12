"""Desktop panel payloads, driven through the shared RUNTIME against the fake Hevy API, plus the dashboard
routes loaded the way Hermes loads them. No network."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import re
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, ROOT, TODAY, TZ, fixture, paged

from hermes_hevy import panel, training
from hermes_hevy.client import KEY_PAGE
from hermes_hevy.dates import parse_instant

BENCH = "79D0BB3A"
BENCH_ENGLISH = "Bench Press (Barbell)"
BENCH_OWN = "Press de banca (barra)"
CARD_IDS = ["sessions", "working_sets", "volume", "duration"]
OVERVIEW_KEYS = {"state", "date", "generated_at", "synced_at", "status", "week", "signals", "last_workout",
                 "records", "consistency"}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")


def load(*, sessions=3, sets=18, volume=18250.0, minutes=185.0, base_sessions=3.2, base_sets=24.0,
         base_volume=20000.0, by_muscle=(), flags=()) -> dict:
    """A training_load result built by hand, for the pure helpers."""
    return {
        "window": {"start": "2026-11-03", "end": "2026-11-09", "days": 7},
        "sessions": sessions, "working_sets": sets, "volume_kg": volume, "duration_min": minutes,
        "baseline": {"start": "2026-10-06", "end": "2026-11-02", "sessions": base_sessions,
                     "working_sets": base_sets, "volume_kg": base_volume},
        "by_muscle": list(by_muscle),
        "push_pull": {"push_sets": 10.0, "pull_sets": 8.0, "ratio": 1.25},
        "flags": list(flags),
    }


def card(cards: list, card_id: str) -> dict:
    return next(c for c in cards if c["id"] == card_id)


def newest_workout() -> dict:
    return max(fixture("workouts"), key=lambda w: parse_instant(w["start_time"]))


# -- overview on the synthetic data ----------------------------------------------------------------

def test_overview_has_every_top_level_key(runtime, hevy_data):
    out = panel.overview(runtime)
    assert out["state"] == "ok"
    assert set(out) == OVERVIEW_KEYS
    assert out["date"] == TODAY.isoformat()
    assert out["synced_at"] == "2026-11-09T18:00"  # the fake clock, 17:00 UTC, in Madrid
    assert STAMP.fullmatch(out["generated_at"])
    assert out["status"]["state"] == "ok" and out["status"]["synced_at"] == out["synced_at"]
    week = out["week"]
    assert set(week) == {"start", "end", "days", "baseline_label", "cards", "muscles", "push_pull"}
    assert (week["start"], week["end"], week["days"]) == ("2026-11-03", "2026-11-09", 7)
    assert week["baseline_label"] == "4-week average"


def test_overview_cards_muscles_and_push_pull(runtime, hevy_data):
    week = panel.overview(runtime)["week"]
    assert [c["id"] for c in week["cards"]] == CARD_IDS
    assert all("tone" not in c for c in week["cards"])
    assert 0 < len(week["muscles"]) <= 10
    sets = [m["sets"] for m in week["muscles"]]
    assert sets == sorted(sets, reverse=True)
    assert set(week["push_pull"]) == {"push_sets", "pull_sets", "ratio"}


def test_overview_signals_are_warnings(runtime, hevy_data):
    for signal in panel.overview(runtime)["signals"]:
        assert set(signal) == {"kind", "tone", "text"}
        assert signal["tone"] == "warn" and signal["text"]


def test_overview_last_workout_is_the_newest(runtime, hevy_data):
    newest = newest_workout()
    last = panel.overview(runtime)["last_workout"]
    assert last["id"] == newest["id"] and last["title"] == newest["title"]
    assert last["date"] == "2026-11-06"
    assert len(last["exercises"]) <= 8
    assert last["more_exercises"] == max(0, len(newest["exercises"]) - 8)
    assert set(last["exercises"][0]) == {"title", "working_sets", "best_set", "best_e1rm_kg"}


def test_overview_records_are_capped_and_have_no_volume(runtime, hevy_data):
    records = panel.overview(runtime)["records"]
    assert set(records) == {"days", "total", "items"} and records["days"] == 30
    assert records["total"] >= len(records["items"]) > 0
    assert len(records["items"]) <= 6
    assert all(item["kind"] in {"e1rm", "weight", "reps"} for item in records["items"])
    keys = [(item["date"], item["kind"]) for item in records["items"]]
    assert [k[0] for k in keys] == sorted((k[0] for k in keys), reverse=True)


def test_overview_consistency_has_twelve_weeks_oldest_first(runtime, hevy_data):
    consistency = panel.overview(runtime)["consistency"]
    assert set(consistency) == {"weeks", "avg_sessions_per_week", "current_streak_weeks",
                                "days_since_last_workout", "last_workout_date", "usual_days"}
    weeks = consistency["weeks"]
    assert len(weeks) == 12
    assert set(weeks[0]) == {"week_start", "sessions"}
    starts = [date.fromisoformat(w["week_start"]) for w in weeks]
    assert starts == sorted(starts) and all(s.weekday() == 0 for s in starts)
    assert starts[-1] == TODAY - timedelta(days=TODAY.weekday())
    assert consistency["last_workout_date"] == "2026-11-06"
    assert consistency["days_since_last_workout"] == 3


def test_fresh_forces_a_sync_and_plain_reads_do_not(runtime, hevy_data, fake):
    panel.overview(runtime)  # the first read is a full sync
    fake.requests.clear()
    panel.overview(runtime)
    assert not fake.calls("/v1/workouts/events")
    panel.overview(runtime, fresh=True)
    assert fake.calls("/v1/workouts/events")


def test_status_fields_without_check(runtime, hevy_data):
    out = panel.status(runtime)
    assert out["state"] == "ok" and out["plugin"] == "hevy" and out["version"] == "0.1.0"
    assert out["key_configured"] is True and out["key_env"] == "HEVY_API_KEY" and out["key_page"] == KEY_PAGE
    assert out["confirm_writes"] is True
    assert out["timezone_source"] in {"setting", "hermes", "server"}
    assert "account" not in out


def test_status_synced_at_is_null_before_the_first_sync(runtime):
    assert panel.status(runtime)["synced_at"] is None


def test_status_check_adds_the_account(runtime, hevy_data):
    out = panel.status(runtime, check=True)
    assert out["state"] == "ok"
    assert out["account"] == {"name": "Test Lifter", "is_pro": True, "weight_unit": "kg"}


# -- pure helpers ----------------------------------------------------------------------------------

def test_badge_math_and_real_minus_sign():
    cards = panel.cards(load(), 0.0)
    sessions = card(cards, "sessions")
    assert sessions["badge"] == "−6%"  # 3 against 3.2 is about -6%, with a real minus sign
    assert sessions["badge_label"] == "−6% vs your 4-week average"
    assert sessions["delta_pct"] == pytest.approx(-6.25, abs=0.1)
    assert sessions["baseline"] == 3.2 and sessions["baseline_display"] == "3.2"
    assert sessions["detail"] == "avg 3.2 / week"

    up = card(panel.cards(load(sets=24, base_sets=20.0), 0.0), "working_sets")
    assert up["badge"] == "+20%" and up["delta_pct"] == 20.0


def test_badge_is_plus_minus_zero_inside_the_band():
    steady = card(panel.cards(load(sessions=10, base_sessions=10.02), 0.0), "sessions")
    assert steady["badge"] == "±0%"
    assert steady["badge_label"] == "±0% vs your 4-week average"


def test_zero_or_missing_baseline_gives_no_badge():
    zero = card(panel.cards(load(base_sets=0.0), 0.0), "working_sets")
    assert zero["baseline"] == 0.0
    assert zero["delta_pct"] is None and zero["badge"] is None and zero["badge_label"] is None
    no_time = card(panel.cards(load(), 0.0), "duration")
    assert no_time["badge"] is None and no_time["delta_pct"] is None


def test_duration_display_is_h_mm():
    assert panel._hmm(185) == "3:05"
    assert panel._hmm(0) == "0:00"
    assert card(panel.cards(load(minutes=185.0), 0.0), "duration")["display"] == "3:05"
    empty = card(panel.cards(load(minutes=0.0), 0.0), "duration")
    assert empty["display"] == "0:00" and empty["unit"] == "h"


def test_card_numbers_and_units():
    cards = panel.cards(load(volume=18250.0, base_volume=17000.0), 0.0)
    volume = card(cards, "volume")
    assert volume["display"] == "18,250" and volume["unit"] == "kg"
    assert volume["baseline_display"] == "17,000"
    assert volume["detail"] == "avg 17,000 kg / week"
    assert card(cards, "working_sets")["display"] == "18"


def test_record_display_formatting():
    assert panel._kg(100.0) == "100 kg"
    assert panel._kg(101.3) == "101.3 kg"
    assert panel._kg(1000.0) == "1,000 kg"
    assert panel._reps(15) == "15 reps"
    assert panel._reps(1) == "1 rep"


def test_muscles_are_capped_sorted_and_labelled():
    rows = [{"muscle": f"muscle_{i}", "sets": float(i), "baseline_sets": 0.0, "ratio": None} for i in range(12)]
    rows += [{"muscle": "upper_back", "sets": 0.0, "baseline_sets": 4.0, "ratio": 0.0},  # missed: kept
             {"muscle": "neck", "sets": 0.0, "baseline_sets": 0.4, "ratio": 0.0}]  # too small: dropped
    out = panel.muscles(load(by_muscle=rows))
    assert len(out) == 10
    assert [m["sets"] for m in out] == sorted((m["sets"] for m in out), reverse=True)
    assert out[0]["muscle"] == "muscle_11" and out[0]["label"] == "Muscle 11"
    assert all(m["muscle"] != "neck" for m in out)
    missed = panel.muscles(load(by_muscle=[{"muscle": "upper_back", "sets": 0.0, "baseline_sets": 4.0,
                                            "ratio": 0.0}]))
    assert missed == [{"muscle": "upper_back", "label": "Upper back", "sets": 0.0, "baseline_sets": 4.0}]


def test_signals_keep_every_flag_text_as_a_warning():
    flags = [{"kind": "muscle_missed", "direction": "down", "text": "No hamstrings sets this week"},
             {"kind": "sets_vs_baseline", "direction": "up", "text": "Working sets 1.4x"}]
    assert panel.signals(load(flags=flags)) == [
        {"kind": "muscle_missed", "tone": "warn", "text": "No hamstrings sets this week"},
        {"kind": "sets_vs_baseline", "tone": "warn", "text": "Working sets 1.4x"},
    ]


def test_own_titles_take_the_newest_workout_and_skip_blanks():
    old = {"start_time": "2026-10-01T10:00:00Z", "exercises": [
        {"exercise_template_id": BENCH, "title": "Bench A"}]}
    new = {"start_time": "2026-11-01T10:00:00Z", "exercises": [
        {"exercise_template_id": BENCH, "title": BENCH_OWN}, {"exercise_template_id": "X", "title": " "}]}
    assert panel._own_titles([new, old]) == {BENCH: BENCH_OWN}  # the list is newest first


def test_baseline_duration_matches_the_training_window():
    workouts = fixture("workouts")
    window_start = TODAY - timedelta(days=6)
    base = [w for w in workouts
            if window_start - timedelta(days=28) <= training._local_day(w, TZ) <= window_start - timedelta(days=1)]
    expected = sum(training._duration_min(w) for w in base) * 7 / 28
    assert panel.baseline_duration_min(workouts, TZ, TODAY) == pytest.approx(expected)


def test_consistency_helper_passes_only_the_panel_fields():
    workouts = fixture("workouts")
    out = panel.consistency(workouts, TZ, TODAY)
    assert len(out["weeks"]) == 12 and all(set(w) == {"week_start", "sessions"} for w in out["weeks"])
    assert "longest_gap_days" not in out


# -- records: the user's own title --------------------------------------------------------------

def test_records_exercise_uses_the_workouts_own_title(runtime, hevy_data, fake):
    workouts = copy.deepcopy(fixture("workouts"))
    for workout in workouts:
        for exercise in workout["exercises"]:
            if exercise["exercise_template_id"] == BENCH:
                exercise["title"] = BENCH_OWN
    fake.routes["/v1/workouts"] = paged("workouts", workouts)
    templates = {t["id"]: t for t in fixture("exercise_templates")}
    items = panel.records(workouts, TZ, templates, TODAY)["items"]
    names = {item["exercise"] for item in items}
    assert BENCH_ENGLISH not in names
    assert BENCH_OWN in names

    # Through the route too: the panel shows the user's title, not Hevy's.
    out = panel.overview(runtime)
    assert BENCH_ENGLISH not in {item["exercise"] for item in out["records"]["items"]}


# -- states ----------------------------------------------------------------------------------------

def test_no_key_is_not_configured_without_network(runtime, hevy_data, fake, monkeypatch):
    monkeypatch.setattr(runtime, "_key_provider", lambda: None)
    out = panel.overview(runtime)
    assert out["state"] == "not_configured" and out["key_configured"] is False
    assert out["key_env"] == "HEVY_API_KEY" and out["key_page"] == KEY_PAGE and out["message"]
    assert not fake.requests
    status = panel.status(runtime, check=True)
    assert status["state"] == "not_configured" and status["key_configured"] is False
    assert "account" not in status and not fake.requests


def test_401_on_workouts_is_auth_error(runtime, hevy_data, fake):
    fake.routes["/v1/workouts"] = httpx.Response(401, json={"error": f"bad key {API_KEY}"})
    out = panel.overview(runtime)
    assert out["state"] == "auth_error" and out["key_env"] == "HEVY_API_KEY" and out["key_page"] == KEY_PAGE
    assert "week" not in out


def test_403_on_workouts_is_plan_required(runtime, hevy_data, fake):
    fake.routes["/v1/workouts"] = httpx.Response(403, json={"error": "Pro required"})
    out = panel.overview(runtime)
    assert out["state"] == "plan_required" and out["key_env"] == "HEVY_API_KEY"


def test_check_failure_keeps_key_configured(runtime, hevy_data, fake):
    fake.routes["/v1/user/info"] = httpx.Response(401, json={"error": "nope"})
    out = panel.status(runtime, check=True)
    assert out["state"] == "auth_error" and out["key_configured"] is True
    assert "account" not in out and out["message"]


def test_api_key_never_appears_in_any_payload(runtime, hevy_data, fake):
    payloads = [panel.overview(runtime, fresh=True), panel.status(runtime), panel.status(runtime, check=True)]
    fake.routes["/v1/workouts"] = httpx.Response(401, json={"error": f"echo {API_KEY}"})
    payloads.append(panel.overview(runtime))
    fake.routes["/v1/user/info"] = httpx.Response(403, json={"error": f"echo {API_KEY}"})
    payloads.append(panel.status(runtime, check=True))
    for payload in payloads:
        assert API_KEY not in json.dumps(payload)


# -- dashboard/plugin_api.py -----------------------------------------------------------------------

class _StubRouter:
    """Enough of ``fastapi.APIRouter`` to collect the routes when FastAPI is not installed."""

    def __init__(self):
        self.routes = {}

    def get(self, path, **_kw):
        def decorate(fn):
            self.routes[path] = fn
            return fn
        return decorate


def _load_api(monkeypatch):
    try:
        import fastapi  # noqa: F401
    except ImportError:
        stub = types.ModuleType("fastapi")
        stub.APIRouter = _StubRouter
        monkeypatch.setitem(sys.modules, "fastapi", stub)
    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_hevy_test",
                                                  ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch, hevy_data, clock, sleeps, tmp_path):
    module = _load_api(monkeypatch)
    client_mod = importlib.import_module(module._PACKAGE_NAME + ".client")
    store_mod = importlib.import_module(module._PACKAGE_NAME + ".store")
    client = client_mod.HevyClient(lambda: API_KEY, transport=httpx.MockTransport(hevy_data.handler),
                                   clock=clock, sleep=sleeps.append)
    rt = module.RUNTIME
    monkeypatch.setattr(rt, "_client", client)
    monkeypatch.setattr(rt, "_key_provider", lambda: API_KEY)
    monkeypatch.setattr(rt, "today", lambda: TODAY)
    monkeypatch.setattr(rt, "tz", lambda: TZ)
    monkeypatch.setattr(rt, "_get_config", lambda key, default=None: default)
    monkeypatch.setattr(rt, "store", store_mod.WorkoutStore(lambda: tmp_path, clock=clock))
    return module


def test_plugin_api_routes(api, monkeypatch):
    assert api.overview()["state"] == "ok"
    assert api.overview(fresh="true")["state"] == "ok"
    assert api.status(check="true")["account"]["name"] == "Test Lifter"
    monkeypatch.setattr(api.RUNTIME, "_key_provider", lambda: None)
    assert api.overview()["state"] == "not_configured"
    assert api.status()["key_configured"] is False


def test_plugin_api_package_is_private(api):
    # Loaded by path under its own name: never shadows or rebinds the agent half's modules.
    assert api._PACKAGE_NAME.startswith("_hevy_panel_")
    assert api.panel.__name__ == api._PACKAGE_NAME + ".panel"


def test_plugin_api_over_http(api):
    testclient = pytest.importorskip("fastapi.testclient")
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/hevy")
    http = testclient.TestClient(app)
    for path in ("/overview", "/overview?fresh=true", "/status?check=true"):
        response = http.get("/api/plugins/hevy" + path)
        assert response.status_code == 200, path
        assert response.json()["state"] == "ok", path
        assert API_KEY not in response.text
    assert "account" in http.get("/api/plugins/hevy/status?check=true").json()
