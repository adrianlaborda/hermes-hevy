"""Payloads for the Hermes Desktop panel (``dashboard/plugin_api.py`` serves them).

Read-only, and built from the same workout cache and analysis the tools use. Every function returns a
JSON-ready dict with a ``state``: ``ok``, ``not_configured``, ``auth_error``, ``plan_required``,
``rate_limited`` or ``error``, so the panel can render onboarding or an error instead of receiving an
HTTP failure. Nothing returned ever contains the API key.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, tzinfo
from functools import wraps
from typing import Any, Callable, Mapping, Optional

from . import __version__, summaries, training
from .client import (KEY_ENV, KEY_PAGE, KEY_SETTINGS, AuthError, HevyError, LimitError, NotConfiguredError,
                     RateLimitedError)
from .runtime import PLUGIN_ID, Runtime
from .training import _base_label

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7  # the "this week" cards
BASELINE_DAYS = 28  # the 4 weeks before the window, scaled to the window's length
CONSISTENCY_WEEKS = 12
RECORDS_DAYS = 30
RECORDS_LIMIT = 6
RECORD_KINDS = ("e1rm", "weight", "reps")  # the panel leaves volume PRs out: accessories flood the list
RECORD_RANK = {kind: rank for rank, kind in enumerate(RECORD_KINDS)}  # same-day order: e1rm, weight, reps
RECORD_LABELS = {"e1rm": "Est. 1RM", "weight": "Heaviest set", "reps": "Most reps"}
MUSCLE_LIMIT = 10
EXERCISE_LIMIT = 8  # exercises shown for the last workout
SIGNAL_TONE = "warn"
BADGE_BAND_PCT = 0.5  # changes smaller than this against the baseline read as "±0%"


def _envelope(state: str, message: str = "", **extra: Any) -> dict:
    out: dict = {"state": state}
    if message:
        out["message"] = message
    out.update(extra)
    return out


def _error_envelope(exc: HevyError) -> dict:
    """The state for a Hevy failure; the key page and variable only where they help the user fix it."""
    if isinstance(exc, NotConfiguredError):
        return _envelope("not_configured", str(exc), key_page=KEY_PAGE, key_env=KEY_ENV)
    if isinstance(exc, AuthError):
        return _envelope("auth_error", str(exc), key_page=KEY_PAGE, key_env=KEY_ENV)
    if isinstance(exc, LimitError):
        return _envelope("plan_required", str(exc), key_page=KEY_PAGE, key_env=KEY_ENV)
    if isinstance(exc, RateLimitedError):
        return _envelope("rate_limited", str(exc))
    return _envelope("error", str(exc))


def guarded(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Turn Hevy failures into a renderable ``state``; never raise into the web server."""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except HevyError as exc:
            return _error_envelope(exc)
        except (TypeError, ValueError) as exc:
            return _envelope("error", f"Invalid request: {exc}")
        except Exception as exc:  # defensive: logged with the traceback, the panel still gets an answer
            logger.exception("hevy panel %s failed", fn.__name__)
            return _envelope("error", f"Unexpected error in the Hevy plugin ({type(exc).__name__}).")
    return wrapper


def _stamp(epoch: Optional[float], tz: tzinfo) -> Optional[str]:
    """Epoch seconds as local ``YYYY-MM-DDTHH:MM``; None when there is no time."""
    return datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%dT%H:%M") if epoch else None


# -- routes ---------------------------------------------------------------------------------------

def status(rt: Runtime, *, check: bool = False) -> dict:
    """Key configured, time zone, cache age and, with ``check``, the Hevy account (plan, units)."""
    key = rt.api_key()
    out = _envelope("ok", plugin=PLUGIN_ID, version=__version__, key_configured=bool(key),
                    key_page=KEY_PAGE, key_env=KEY_ENV)
    if not key:
        out.update(state="not_configured",
                   message=f"No Hevy API key is configured. Create one at {KEY_PAGE} (Hevy Pro) and paste it "
                           f"in {KEY_SETTINGS}.")
        return out
    choice = rt.zone_choice()
    out.update(timezone=choice.name, timezone_source=choice.source,
               confirm_writes=rt.flag("confirm_writes", True))
    client = rt.client()
    out["synced_at"] = _stamp(rt.store.last_sync(client), rt.tz())
    if check:
        try:
            info = (client.get("/v1/user/info", ttl=0) or {}).get("data") or {}
        except HevyError as exc:
            out.update(_error_envelope(exc))
        else:
            out["account"] = {"name": info.get("name") or info.get("username"), "is_pro": info.get("is_pro"),
                              "weight_unit": info.get("weight_unit", "kg")}
    return out


@guarded
def overview(rt: Runtime, *, fresh: bool = False) -> dict:
    """Everything the panel shows in one request: status, this week's cards, muscles and signals, the last
    workout, recent records and consistency. ``fresh`` syncs the workout cache before reading it."""
    current = status(rt)
    if current["state"] != "ok":
        return current
    client = rt.client()
    tz, today = rt.tz(), rt.today()
    if fresh:
        rt.store.sync(client, force=True)
    # One read for every part: the store returns workouts newest first, and each helper filters in memory.
    workouts = rt.store.workouts(client)
    templates = rt.store.templates(client)
    current["synced_at"] = _stamp(rt.store.last_sync(client), tz)
    load = training.training_load(workouts, templates, tz, today=today, days=WINDOW_DAYS,
                                  baseline_days=BASELINE_DAYS)
    return _envelope(
        "ok",
        date=today.isoformat(),
        generated_at=datetime.now(tz).strftime("%Y-%m-%dT%H:%M"),
        synced_at=current["synced_at"],
        status=current,
        week={
            "start": load["window"]["start"],
            "end": load["window"]["end"],
            "days": WINDOW_DAYS,
            "baseline_label": f"{_base_label(BASELINE_DAYS)} average",
            "cards": cards(load, baseline_duration_min(workouts, tz, today)),
            "muscles": muscles(load),
            "push_pull": load["push_pull"],
        },
        signals=signals(load),
        last_workout=last_workout(workouts, tz, templates),
        records=records(workouts, tz, templates, today),
        consistency=consistency(workouts, tz, today),
    )


# -- building blocks ------------------------------------------------------------------------------

def _title(text: Any) -> str:
    return str(text or "").replace("_", " ").strip().capitalize()


def _kg(value: float) -> str:
    """100.0 -> "100 kg", 101.25 -> "101.3 kg": one decimal, dropped when the weight is whole."""
    text = f"{round(float(value), 1):,.1f}".rstrip("0").rstrip(".")
    return f"{text} kg"


def _reps(value: float) -> str:
    count = int(value)
    return f"{count} rep" if count == 1 else f"{count} reps"


def _hmm(minutes: float) -> str:
    """Minutes as ``h:mm``: 185 -> "3:05", 0 -> "0:00"."""
    total = int(round(minutes))
    return f"{total // 60}:{total % 60:02d}"


def _one_decimal(value: float) -> str:
    """3.2 -> "3.2", 10.0 -> "10"."""
    return f"{round(value, 1):g}"


def baseline_duration_min(workouts: list, tz: tzinfo, today: date) -> float:
    """Minutes trained per week over the baseline days before the window, as ``training_load`` scales its
    sets and volume. ``training_load`` does not report duration for the baseline, so it is read here."""
    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    base_start = window_start - timedelta(days=BASELINE_DAYS)
    base_end = window_start - timedelta(days=1)
    total = 0.0
    for workout in workouts:
        day = training._local_day(workout, tz)
        if day is not None and base_start <= day <= base_end:
            total += training._duration_min(workout)
    return total * WINDOW_DAYS / BASELINE_DAYS


def _badge(delta: float) -> str:
    if abs(delta) < BADGE_BAND_PCT:
        return "±0%"
    return f"{'+' if delta > 0 else '−'}{abs(delta):.0f}%"


def _card(card_id: str, label: str, value: float, baseline: Optional[float], *, display: str,
          baseline_display: Optional[str], unit: str, detail: str) -> dict:
    """One week card. Without a baseline (missing or 0) there is no delta, badge or badge label."""
    delta = badge = badge_label = None
    if baseline:
        delta = round(100 * (value - baseline) / baseline, 1)
        badge = _badge(delta)
        badge_label = f"{badge} vs your {_base_label(BASELINE_DAYS)} average"
    return {
        "id": card_id, "label": label, "value": value, "display": display, "unit": unit,
        "baseline": round(baseline, 1) if baseline is not None else None,
        "baseline_display": baseline_display, "delta_pct": delta, "badge": badge,
        "badge_label": badge_label, "detail": detail,
    }


def cards(load: Mapping[str, Any], baseline_duration_min: float) -> list[dict]:
    """Sessions, working sets, volume and time for the week, each against the baseline."""
    base = load["baseline"]
    sessions, sets = load["sessions"], load["working_sets"]
    volume, minutes = load["volume_kg"], load["duration_min"]
    base_sessions, base_sets, base_volume = base["sessions"], base["working_sets"], base["volume_kg"]
    sessions_base, sets_base = _one_decimal(base_sessions), _one_decimal(base_sets)
    volume_base = f"{round(base_volume):,}"
    time_base = _hmm(baseline_duration_min)
    return [
        _card("sessions", "Sessions", sessions, base_sessions, display=f"{sessions:,}", baseline_display=sessions_base,
              unit="", detail=f"avg {sessions_base} / week"),
        _card("working_sets", "Working sets", sets, base_sets, display=f"{sets:,}", baseline_display=sets_base,
              unit="", detail=f"avg {sets_base} / week"),
        _card("volume", "Volume", volume, base_volume, display=f"{round(volume):,}", baseline_display=volume_base,
              unit="kg", detail=f"avg {volume_base} kg / week"),
        _card("duration", "Time", minutes, baseline_duration_min, display=_hmm(minutes),
              baseline_display=time_base, unit="h", detail=f"avg {time_base} / week"),
    ]


def muscles(load: Mapping[str, Any]) -> list[dict]:
    """Muscles trained this week or usually trained, busiest first, at most ten."""
    rows = [m for m in load["by_muscle"] if m["sets"] > 0 or m["baseline_sets"] >= 1]
    rows.sort(key=lambda m: (-m["sets"], -m["baseline_sets"]))
    return [{"muscle": m["muscle"], "label": _title(m["muscle"]), "sets": m["sets"],
             "baseline_sets": m["baseline_sets"]} for m in rows[:MUSCLE_LIMIT]]


def signals(load: Mapping[str, Any]) -> list[dict]:
    """One warning per training-load flag, with the flag's own text."""
    return [{"kind": flag["kind"], "tone": SIGNAL_TONE, "text": flag["text"]} for flag in load["flags"]]


def last_workout(workouts: list, tz: tzinfo, templates: Mapping[str, Any]) -> Optional[dict]:
    """The newest workout (``workouts`` is newest first), its first exercises and what was cut."""
    if not workouts:
        return None
    detail = summaries.workout_detail(workouts[0], tz, templates)
    exercises = detail["exercises"]
    return {
        "id": detail["id"],
        "title": detail["title"],
        "date": detail["date"],
        "start_local": detail["start_local"],
        "duration_hmm": detail["duration_hmm"],
        "working_sets": detail["working_sets"],
        "volume_kg": detail["volume_kg"],
        "exercises": [{"title": e["title"], "working_sets": e["working_sets"], "best_set": e.get("best_set"),
                       "best_e1rm_kg": e.get("best_e1rm_kg")} for e in exercises[:EXERCISE_LIMIT]],
        "more_exercises": max(0, len(exercises) - EXERCISE_LIMIT),
    }


def _own_titles(workouts: list) -> dict[str, str]:
    """Each exercise template's title as the user logged it, from the newest workout that contains it."""
    titles: dict[str, str] = {}
    for workout in workouts:  # newest first
        for exercise in workout.get("exercises") or []:
            template_id = exercise.get("exercise_template_id")
            title = str(exercise.get("title") or "").strip()
            if template_id and title:
                titles.setdefault(template_id, title)
    return titles


def _record_value(kind: str, value: float) -> str:
    return _reps(value) if kind == "reps" else _kg(value)


def records(workouts: list, tz: tzinfo, templates: Mapping[str, Any], today: date) -> dict:
    """Personal records in the last 30 days (e1RM, heaviest set, most reps), newest first, at most six.
    PR detection compares each session with the whole history, so ``workouts`` must be all of it."""
    since = today - timedelta(days=RECORDS_DAYS - 1)
    own = _own_titles(workouts)
    found = [r for r in summaries.personal_records(workouts, tz, since=since, templates=templates)
             if r["kind"] in RECORD_KINDS]
    found.sort(key=lambda r: (r["date"], -RECORD_RANK[r["kind"]]), reverse=True)
    items = [{
        "date": r["date"],
        "exercise": own.get(r["exercise_template_id"]) or r["exercise"],
        "kind": r["kind"],
        "kind_label": RECORD_LABELS[r["kind"]],
        "display": _record_value(r["kind"], r["value"]),
        "previous_display": _record_value(r["kind"], r["previous"]),
        "workout_id": r["workout_id"],
    } for r in found[:RECORDS_LIMIT]]
    return {"days": RECORDS_DAYS, "total": len(found), "items": items}


def consistency(workouts: list, tz: tzinfo, today: date) -> dict:
    """Sessions per week for the last 12 weeks, plus streak, recency and the usual training days."""
    raw = training.consistency(workouts, tz, today=today, weeks=CONSISTENCY_WEEKS)
    return {
        "weeks": [{"week_start": w["week_start"], "sessions": w["sessions"]} for w in raw["weeks"]],
        "avg_sessions_per_week": raw["avg_sessions_per_week"],
        "current_streak_weeks": raw["current_streak_weeks"],
        "days_since_last_workout": raw["days_since_last_workout"],
        "last_workout_date": raw["last_workout_date"],
        "usual_days": raw["usual_days"],
    }
