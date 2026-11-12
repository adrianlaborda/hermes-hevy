"""Model-facing tools. Reads answer from the local cache; writes go to Hevy after the user approves them.

Each tool returns a JSON string and never raises: domain errors come back as ``{"error": ...}``."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Callable, Mapping, Optional

from . import summaries, training, writes
from .client import HevyClient, HevyError, NotFoundError
from .dates import ArgError, parse_day, parse_instant, resolve_range
from .runtime import RUNTIME
from .summaries import compact_set, workout_detail, workout_summary

logger = logging.getLogger(__name__)

MAX_WORKOUT_DAYS = 366
MAX_PROGRESS_DAYS = 1830
MAX_MEASUREMENT_DAYS = 3650
MAX_RECORDS = 40
BASELINE_DAYS = 28
RECENT_DAYS = 90
ROUTINE_TTL = 300.0
RECORD_KINDS = ("e1rm", "weight", "reps", "volume")  # also the display priority on the same day
DEFAULT_RECORD_KINDS = ("e1rm", "weight", "reps")  # volume PRs are opt-in: accessories flood the list

WRITE_NOTICE = ("Writes to the user's Hevy account. Only call it when the user asked for this change; "
                "Hermes asks the user to approve it before it runs.")

_DATE = {"type": "string", "description": "A single day, YYYY-MM-DD, 'today' or 'yesterday'. Default today."}
_START = {"type": "string", "description": "First day of a range (YYYY-MM-DD). Use with end_date."}
_END = {"type": "string", "description": "Last day of a range (YYYY-MM-DD), inclusive. Default today."}
_DAYS = {"type": "integer", "minimum": 1, "description": "Instead of dates: the last N days ending today."}
_RANGE = {"date": _DATE, "start_date": _START, "end_date": _END, "days": _DAYS}
_RAW = {"type": "boolean", "description": "Also return Hevy's raw objects under 'raw'. Large; only when the "
        "summary lacks a field you need."}
_QUERY = {"type": "string", "description": "Case-insensitive text to match in titles."}
_EXERCISE_NAME = ("Exercise name or id: Hevy's English title (\"Seated Leg Curl (Machine)\"), the name shown in "
                  "the user's own workouts (any language), or the template id. Run hevy_exercises when unsure.")
_EXERCISE = {"type": "string", "description": _EXERCISE_NAME}
_LOCAL_TIME = {"type": "string", "description": "Local time in the user's time zone, 'YYYY-MM-DD HH:MM', or ISO "
               "8601 with an offset or Z."}


def _limit(maximum: int, default: int) -> dict:
    return {"type": "integer", "minimum": 1, "maximum": maximum,
            "description": f"Most rows to return. Default {default}."}


def _schema(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}


def _tool(fn: Callable[[dict], dict]) -> Callable[..., str]:
    # Not functools.wraps: Hermes reads the handler signature, which must keep ``**kwargs``.
    def handler(args: dict | None = None, **_kwargs: Any) -> str:
        try:
            result = fn(dict(args or {}))
        except (ArgError, HevyError) as exc:
            result = {"error": str(exc)}
        except (TypeError, ValueError) as exc:
            result = {"error": f"Invalid argument: {exc}"}
        except Exception as exc:
            logger.exception("hevy tool %s failed", fn.__name__)
            result = {"error": f"Unexpected error in the Hevy plugin ({type(exc).__name__})."}
        return json.dumps(result, ensure_ascii=False, default=str)
    handler.__name__ = handler.__qualname__ = fn.__name__
    handler.__doc__ = fn.__doc__
    return handler


# -- shared helpers ------------------------------------------------------------------------------

def _client() -> HevyClient:
    return RUNTIME.client()


def _templates(client: HevyClient) -> dict:
    return RUNTIME.store.templates(client)


def _aliases(client: HevyClient) -> dict[str, str]:
    """Titles the user's workouts give exercises whose English title differs, mapped to the template id.

    Newest workout first, so a title that now points at two ids keeps the most recent one."""
    templates = _templates(client)
    aliases: dict[str, str] = {}
    seen: set[str] = set()
    for workout in RUNTIME.store.workouts(client):
        for ex in workout.get("exercises") or []:
            title = str(ex.get("title") or "").strip()
            template_id = ex.get("exercise_template_id")
            key = title.casefold()
            if not title or not template_id or key in seen:
                continue
            seen.add(key)
            english = str((templates.get(template_id) or {}).get("title") or "")
            if english.casefold() == key:
                continue
            aliases[title] = str(template_id)
    return aliases


def _alias_by_id(aliases: Mapping[str, str]) -> dict[str, str]:
    """The user's most recent title for each template id (``_aliases`` is ordered newest first)."""
    by_id: dict[str, str] = {}
    for title, template_id in aliases.items():
        by_id.setdefault(template_id, title)
    return by_id


def _window(start: date, end: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Local calendar days ``start..end`` (inclusive) as instants the store can compare with start_time."""
    return datetime.combine(start, time.min, tz), datetime.combine(end + timedelta(days=1), time.min, tz)


def _count(args: dict, key: str, default: int, low: int, high: int) -> int:
    value = args.get(key)
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ArgError(f"{key} must be a whole number from {low} to {high}.") from None
    if not low <= number <= high:
        raise ArgError(f"{key} must be from {low} to {high}, not {number}.")
    return number


def _choice_arg(args: dict, key: str, allowed: tuple[str, ...]) -> Optional[str]:
    value = args.get(key)
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text not in allowed:
        raise ArgError(f"{key} {value!r} is not one of: {', '.join(allowed)}.")
    return text


def _query(args: dict) -> str:
    return str(args.get("query") or "").strip().lower()


def _matches(workout: dict, needle: str) -> bool:
    if needle in (workout.get("title") or "").lower():
        return True
    return any(needle in (ex.get("title") or "").lower() for ex in workout.get("exercises") or [])


def _totals(rows: list[dict]) -> dict:
    return {
        "sessions": len(rows),
        "working_sets": sum(r["working_sets"] for r in rows),
        "volume_kg": sum(r["volume_kg"] for r in rows),
        "duration_min": sum(r.get("duration_min") or 0 for r in rows),
    }


def _with_raw(result: dict, args: dict, raw: Any) -> dict:
    if args.get("raw"):
        result["raw"] = raw
    return result


def _drop_none(row: dict) -> dict:
    return {k: v for k, v in row.items() if v is not None}


def _unwrap(response: Any, key: str) -> dict:
    """Hevy answers writes either wrapped under ``key`` (sometimes as a one-item list) or bare."""
    value = response.get(key) if isinstance(response, dict) and key in response else response
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        raise HevyError(f"Hevy saved the change but did not send the {key} back. "
                        "Check with the matching read tool before retrying.")
    return value


def _recent_use(workouts: list[dict], tz: tzinfo) -> tuple[dict[str, int], dict[str, str]]:
    """Per exercise template: how many of the given workouts contain it, and the local date of the newest."""
    counts: dict[str, int] = {}
    last: dict[str, str] = {}
    for workout in workouts:  # newest first
        start = parse_instant(workout.get("start_time"))
        if start is None:
            continue
        day = start.astimezone(tz).date().isoformat()
        ids = dict.fromkeys(ex.get("exercise_template_id") for ex in workout.get("exercises") or [])
        for template_id in ids:
            if template_id is None:
                continue
            counts[template_id] = counts.get(template_id, 0) + 1
            last.setdefault(template_id, day)
    return counts, last


def _exercise_row(template_id: str, template: dict, sessions: int, last_done: Optional[str]) -> dict:
    return {
        "id": template.get("id", template_id),
        "title": template.get("title") or "",
        "type": template.get("type"),
        "primary_muscle": template.get("primary_muscle_group"),
        "secondary_muscles": template.get("secondary_muscle_groups") or [],
        "equipment": template.get("equipment"),
        "is_custom": bool(template.get("is_custom")),
        "sessions_90d": sessions,
        "last_done": last_done,
    }


def _routine_set(s: dict) -> dict:
    row = compact_set(s)
    rep_range = s.get("rep_range")
    if rep_range:
        row["rep_range"] = {"start": rep_range.get("start"), "end": rep_range.get("end")}
    return row


def _routine_compact(routine: dict) -> dict:
    exercises = [
        _drop_none({
            "title": e.get("title"),
            "exercise_template_id": e.get("exercise_template_id"),
            "rest_seconds": e.get("rest_seconds"),
            "notes": e.get("notes"),
            "superset_id": e.get("superset_id"),
            "sets": [_routine_set(s) for s in e.get("sets") or []],
        })
        for e in routine.get("exercises") or []
    ]
    return _drop_none({"id": routine.get("id"), "title": routine.get("title") or "",
                       "folder_id": routine.get("folder_id"), "notes": routine.get("notes"),
                       "exercises": exercises})


def _fetch_routine(client: HevyClient, routine_id: str, *, ttl: Optional[float]) -> dict:
    try:
        data = client.get(f"/v1/routines/{routine_id}", ttl=ttl)
    except NotFoundError:
        raise ArgError(f"No routine with id {routine_id}. List the routines with hevy_routines (without an id) "
                       "and use the id from there.") from None
    routine = data.get("routine") if isinstance(data, dict) else None
    if not isinstance(routine, dict):
        raise ArgError(f"No routine with id {routine_id}. List the routines with hevy_routines.")
    return routine


def _measurement_fields() -> dict:
    props = {}
    for field in writes.MEASUREMENT_FIELDS:
        unit = "kg" if field.endswith("_kg") else "%" if field.endswith("percent") else "cm"
        props[field] = {"type": "number", "exclusiveMinimum": 0,
                        "description": f"{field.replace('_', ' ')} in {unit}."}
    props["clear"] = {"type": "array", "items": {"type": "string", "enum": list(writes.MEASUREMENT_FIELDS)},
                      "description": "Measurements to remove from this date's entry. Omitted fields are kept."}
    return props


# -- schema builders -----------------------------------------------------------------------------

def _set_schema(*, routine: bool) -> dict:
    props: dict[str, Any] = {
        "type": {"type": "string", "enum": list(writes.SET_TYPES),
                 "description": "normal (default), warmup (left out of stats), failure or dropset."},
        "weight_kg": {"type": "number", "minimum": 0, "description": "Weight in kilograms. Or weight_lbs."},
        "weight_lbs": {"type": "number", "minimum": 0, "description": "Weight in pounds, stored as kg. Not with "
                       "weight_kg."},
        "reps": {"type": "integer", "minimum": 0},
        "distance_meters": {"type": "number", "minimum": 0},
        "duration_seconds": {"type": "integer", "minimum": 0},
        "custom_metric": {"type": "number", "description": "The exercise's custom metric, when it has one."},
    }
    if routine:
        props["rep_range"] = {
            "type": "object", "description": "Target rep range, such as 8 to 12, instead of reps.",
            "properties": {"start": {"type": "integer", "minimum": 1}, "end": {"type": "integer", "minimum": 1}},
            "required": ["start", "end"],
        }
    else:
        props["rpe"] = {"type": "number", "enum": list(writes.RPE),
                        "description": "Rate of perceived exertion, 6 to 10 in halves."}
    return {"type": "object", "description": "One set.", "properties": props}


def _exercise_schema(*, routine: bool) -> dict:
    props: dict[str, Any] = {
        "exercise": {"type": "string", "description": _EXERCISE_NAME},
        "notes": {"type": "string"},
        "superset_id": {"type": "integer", "description": "Exercises with the same superset_id are a superset."},
        "sets": {"type": "array", "minItems": 1, "items": _set_schema(routine=routine)},
    }
    if routine:
        props["rest_seconds"] = {"type": "integer", "minimum": 0, "description": "Rest after each set, seconds."}
    return {"type": "object", "properties": props, "required": ["exercise", "sets"]}


def _exercises_schema(*, routine: bool, description: str) -> dict:
    return {"type": "array", "minItems": 1, "description": description,
            "items": _exercise_schema(routine=routine)}


# -- read handlers -------------------------------------------------------------------------------

@_tool
def hevy_workouts(args: dict) -> dict:
    client = _client()
    tz, today = RUNTIME.tz(), RUNTIME.today()
    start, end = resolve_range(args, today, default_days=30, max_days=MAX_WORKOUT_DAYS)
    since, until = _window(start, end, tz)
    workouts = RUNTIME.store.workouts(client, since=since, until=until)
    needle = _query(args)
    if needle:
        workouts = [w for w in workouts if _matches(w, needle)]
    templates = _templates(client)
    rows = [workout_summary(w, tz, templates) for w in workouts]
    limit = _count(args, "limit", 20, 1, 100)
    result: dict[str, Any] = {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "count": len(rows),
        "workouts": rows[:limit],
        "totals": _totals(rows),
    }
    if needle:
        result["query"] = args.get("query")
    if not rows:
        suffix = f" matching “{args.get('query')}”" if needle else ""
        result["note"] = f"No workouts from {start} to {end}{suffix}."
    return _with_raw(result, args, workouts[:limit])


@_tool
def hevy_workout(args: dict) -> dict:
    client = _client()
    workout_id = str(args.get("id") or "").strip()
    if not workout_id:
        raise ArgError("id is required: a workout id from hevy_workouts, or 'latest'.")
    if workout_id.lower() == "latest":
        found = RUNTIME.store.workouts(client)
        if not found:
            raise ArgError("No workouts are logged in Hevy yet.")
        workout = found[0]
    else:
        workout = RUNTIME.store.workout(client, workout_id)
        if workout is None:
            raise ArgError(f"No workout with id {workout_id}. List the workouts with hevy_workouts and use "
                           "the id from there.")
    result = workout_detail(workout, RUNTIME.tz(), _templates(client))
    return _with_raw(result, args, workout)


@_tool
def hevy_exercises(args: dict) -> dict:
    client = _client()
    tz, today = RUNTIME.tz(), RUNTIME.today()
    templates = _templates(client)
    needle = _query(args)
    muscle = _choice_arg(args, "muscle", writes.MUSCLE_GROUPS)
    equipment = _choice_arg(args, "equipment", writes.EQUIPMENT_CATEGORIES)
    custom_only = bool(args.get("custom_only"))
    limit = _count(args, "limit", 25, 1, 100)
    recent = RUNTIME.store.workouts(client, since=datetime.combine(today - timedelta(days=RECENT_DAYS - 1),
                                                                  time.min, tz))
    counts, last = _recent_use(recent, tz)
    aliases = _aliases(client)
    alias_by_id = _alias_by_id(aliases)
    by_alias = {tid for title, tid in aliases.items() if needle and needle in title.lower()}
    exact_alias = {tid for title, tid in aliases.items() if needle and title.lower() == needle}

    ranked = []
    for template_id, template in templates.items():
        title = str(template.get("title") or "")
        if needle and needle not in title.lower() and template_id not in by_alias:
            continue
        if muscle and muscle not in [template.get("primary_muscle_group"),
                                     *(template.get("secondary_muscle_groups") or [])]:
            continue
        if equipment and (template.get("equipment") or "").lower() != equipment:
            continue
        if custom_only and not template.get("is_custom"):
            continue
        sessions = counts.get(template_id, 0)
        row = _exercise_row(template_id, template, sessions, last.get(template_id))
        if template_id in alias_by_id:
            row["your_name"] = alias_by_id[template_id]
        exact = bool(needle) and (title.lower() == needle or template_id in exact_alias)
        ranked.append(((0 if exact else 1, -sessions, title.lower()), row))
    ranked.sort(key=lambda pair: pair[0])
    rows = [row for _, row in ranked]
    result: dict[str, Any] = {"count": len(rows), "exercises": rows[:limit]}
    if not rows:
        result["note"] = "No exercises match. Try a shorter query, or create the exercise with hevy_create_exercise."
    return _with_raw(result, args, [templates.get(r["id"]) for r in rows[:limit]])


@_tool
def hevy_exercise_progress(args: dict) -> dict:
    client = _client()
    tz, today = RUNTIME.tz(), RUNTIME.today()
    templates = _templates(client)
    aliases = _aliases(client)
    exercise = writes.resolve_exercise(args.get("exercise"), templates, aliases)
    start, end = resolve_range(args, today, default_days=365, max_days=MAX_PROGRESS_DAYS)
    since, until = _window(start, end, tz)
    workouts = RUNTIME.store.workouts(client, since=since, until=until)
    sessions = summaries.exercise_sessions(workouts, exercise["id"], tz)
    found = {"id": exercise["id"], "title": exercise.get("title") or "",
             "primary_muscle": exercise.get("primary_muscle_group")}
    your_name = _alias_by_id(aliases).get(exercise["id"])
    if your_name is not None:
        found["your_name"] = your_name
    result = {
        "exercise": found,
        "summary": summaries.progress(sessions, today=today),
        "sessions": list(reversed(sessions))[:15],
    }
    return _with_raw(result, args, workouts)


@_tool
def hevy_records(args: dict) -> dict:
    client = _client()
    tz, today = RUNTIME.tz(), RUNTIME.today()
    days = _count(args, "days", 30, 1, MAX_WORKOUT_DAYS)
    since = today - timedelta(days=days - 1)
    kinds = _record_kinds(args)
    workouts = RUNTIME.store.workouts(client)
    records = [r for r in summaries.personal_records(workouts, tz, since=since, templates=_templates(client))
               if r["kind"] in kinds]
    # Newest first; on the same day the strongest kind leads (e1rm, weight, reps, volume).
    records.sort(key=lambda r: (r["date"], -RECORD_KINDS.index(r["kind"])), reverse=True)
    result: dict[str, Any] = {"since": since.isoformat(), "total": len(records), "records": records[:MAX_RECORDS]}
    if len(records) > MAX_RECORDS:
        result["truncated"] = True
    return _with_raw(result, args, workouts)


def _record_kinds(args: dict) -> tuple[str, ...]:
    value = args.get("kinds")
    if value is None:
        return DEFAULT_RECORD_KINDS
    if not isinstance(value, list) or not value:
        raise ArgError(f"kinds must be a non-empty list from: {', '.join(RECORD_KINDS)}.")
    for kind in value:
        if kind not in RECORD_KINDS:
            raise ArgError(f"kinds {kind!r} is not one of: {', '.join(RECORD_KINDS)}.")
    return tuple(dict.fromkeys(value))


@_tool
def hevy_training_load(args: dict) -> dict:
    client = _client()
    tz, today = RUNTIME.tz(), RUNTIME.today()
    days = _count(args, "days", 7, 1, 28)
    window_start = today - timedelta(days=days - 1)
    since, until = _window(window_start - timedelta(days=BASELINE_DAYS), today, tz)
    workouts = RUNTIME.store.workouts(client, since=since, until=until)
    result = training.training_load(workouts, _templates(client), tz, today=today, days=days,
                                    baseline_days=BASELINE_DAYS)
    return _with_raw(result, args, workouts)


@_tool
def hevy_consistency(args: dict) -> dict:
    client = _client()
    weeks = _count(args, "weeks", 12, 2, 52)
    workouts = RUNTIME.store.workouts(client)
    result = training.consistency(workouts, RUNTIME.tz(), today=RUNTIME.today(), weeks=weeks)
    return _with_raw(result, args, workouts)


@_tool
def hevy_routines(args: dict) -> dict:
    client = _client()
    routine_id = str(args.get("id") or "").strip()
    if routine_id:
        routine = _fetch_routine(client, routine_id, ttl=ROUTINE_TTL)
        return _with_raw({"routine": _routine_compact(routine)}, args, routine)
    folders = list(client.paginate("/v1/routine_folders", "routine_folders", page_size=10, ttl=ROUTINE_TTL))
    routines = list(client.paginate("/v1/routines", "routines", page_size=10, ttl=ROUTINE_TTL))
    folder_titles = {str(f.get("id")): f.get("title") for f in folders}
    rows = []
    for r in routines:
        folder_id = r.get("folder_id")
        exercises = r.get("exercises") or []
        rows.append({
            "id": r.get("id"),
            "title": r.get("title") or "",
            "folder": folder_titles.get(str(folder_id)) if folder_id is not None else None,
            "exercises": len(exercises),
            "exercise_titles": [e.get("title") or "" for e in exercises],
            "updated": r.get("updated_at"),
        })
    result = {"folders": [{"id": f.get("id"), "title": f.get("title")} for f in folders], "routines": rows}
    return _with_raw(result, args, routines)


@_tool
def hevy_body_measurements(args: dict) -> dict:
    client = _client()
    start, end = resolve_range(args, RUNTIME.today(), default_days=365, max_days=MAX_MEASUREMENT_DAYS)
    low, high = start.isoformat(), end.isoformat()
    items = list(client.paginate("/v1/body_measurements", "body_measurements", page_size=10, max_pages=60))
    rows = [_drop_none(m) for m in items if isinstance(m, dict) and low <= str(m.get("date") or "") <= high]
    rows.sort(key=lambda m: str(m.get("date") or ""), reverse=True)
    result: dict[str, Any] = {"range": {"start": low, "end": high}, "measurements": rows,
                              "latest": rows[0] if rows else None}
    if not rows:
        result["note"] = f"No body measurements from {low} to {high}."
    return _with_raw(result, args, items)


# -- write handlers ------------------------------------------------------------------------------

@_tool
def hevy_log_workout(args: dict) -> dict:
    client = _client()
    tz = RUNTIME.tz()
    templates = _templates(client)
    body = writes.build_workout(args, templates, tz, aliases=_aliases(client))
    workout = _unwrap(client.post("/v1/workouts", body), "workout")
    RUNTIME.store.put_workout(client, workout)
    return {"ok": True, "workout": workout_summary(workout, tz, templates)}


@_tool
def hevy_update_workout(args: dict) -> dict:
    client = _client()
    tz = RUNTIME.tz()
    workout_id = str(args.get("id") or "").strip()
    if not workout_id:
        raise ArgError("id is required: a workout id from hevy_workouts.")
    current = RUNTIME.store.workout(client, workout_id)
    if current is None:
        raise ArgError(f"No workout with id {workout_id}. List the workouts with hevy_workouts.")
    if "is_private" not in current and args.get("is_private") is None:
        # A PUT overwrites the flag and Hevy's GET omits it: guessing would publish a private workout.
        raise ArgError("Hevy does not report whether this workout is private, and saving it overwrites that "
                       "setting. Ask the user whether it is private (or use what they already said) and call "
                       "again with is_private true or false.")
    templates = _templates(client)
    changes = {k: v for k, v in args.items() if k != "id"}
    body = writes.apply_workout_changes(writes.workout_to_request(current), changes, templates, tz,
                                        aliases=_aliases(client))
    workout = _unwrap(client.put(f"/v1/workouts/{workout_id}", body), "workout")
    RUNTIME.store.put_workout(client, workout)
    return {"ok": True, "workout": workout_summary(workout, tz, templates)}


@_tool
def hevy_save_routine(args: dict) -> dict:
    client = _client()
    templates = _templates(client)
    aliases = _aliases(client)
    routine_id = str(args.get("routine_id") or "").strip()
    changes = {k: v for k, v in args.items() if k != "routine_id"}
    if routine_id:
        current = _fetch_routine(client, routine_id, ttl=0)
        body = writes.apply_routine_changes(writes.routine_to_request(current), changes, templates,
                                            aliases=aliases)
        saved = _unwrap(client.put(f"/v1/routines/{routine_id}", body), "routine")
        action = "updated"
    else:
        body = writes.build_routine(changes, templates, aliases=aliases)
        saved = _unwrap(client.post("/v1/routines", body), "routine")
        action = "created"
    return {"ok": True, "action": action,
            "routine": {"id": saved.get("id"), "title": saved.get("title"),
                        "exercises": len(saved.get("exercises") or [])}}


@_tool
def hevy_create_routine_folder(args: dict) -> dict:
    client = _client()
    title = str(args.get("title") or "").strip()
    if not title:
        raise ArgError("title is required: the name of the new routine folder.")
    folder = _unwrap(client.post("/v1/routine_folders", {"routine_folder": {"title": title}}), "routine_folder")
    return {"ok": True, "folder": {"id": folder.get("id"), "title": folder.get("title")}}


@_tool
def hevy_create_exercise(args: dict) -> dict:
    client = _client()
    body = writes.build_exercise_template(args)
    response = client.post("/v1/exercise_templates", body)
    template_id = response.get("id") if isinstance(response, dict) else None
    if template_id is None:
        raise HevyError("Hevy created the exercise but did not send its id back. Look for it with hevy_exercises.")
    # The library changed: refetch it now instead of waiting for the next age-based refresh.
    RUNTIME.store.templates(client, max_age=0)
    return {"ok": True, "exercise_template_id": str(template_id), "title": body["exercise"]["title"]}


@_tool
def hevy_log_body_measurement(args: dict) -> dict:
    client = _client()
    day = parse_day(args.get("date") or "today", RUNTIME.today()).isoformat()
    # Models often send every field with null: only listed values change, and clearing is explicit.
    changes = {k: v for k, v in args.items() if k in writes.MEASUREMENT_FIELDS and v is not None}
    clear = args.get("clear") or []
    if not isinstance(clear, list) or any(field not in writes.MEASUREMENT_FIELDS for field in clear):
        raise ArgError(f"clear must be a list of measurement names: {', '.join(writes.MEASUREMENT_FIELDS)}.")
    unknown = sorted(k for k in args if k not in (*writes.MEASUREMENT_FIELDS, "date", "clear", "raw"))
    if unknown:
        raise ArgError(f"Unknown measurement {', '.join(map(repr, unknown))}. "
                       f"Use these keys: {', '.join(writes.MEASUREMENT_FIELDS)}.")
    changes.update({field: None for field in clear if field not in changes})
    if not changes:
        raise ArgError("Give at least one measurement, such as weight_kg, or list fields to remove in clear.")
    try:
        existing = client.get(f"/v1/body_measurements/{day}", ttl=0)
    except NotFoundError:
        existing = None
    body = writes.merge_measurement(existing, changes)
    if existing is None:
        client.post("/v1/body_measurements", {"date": day, **body})
    else:
        client.put(f"/v1/body_measurements/{day}", body)
    return {"ok": True, "date": day, "changed": {field: body[field] for field in changes}}


# -- schemas and registry ------------------------------------------------------------------------

_WORKOUT_EXERCISES = _exercises_schema(
    routine=False, description="The exercises in order, each with its sets.")
_ROUTINE_EXERCISES = _exercises_schema(
    routine=True, description="The exercises in order, each with its sets. Replaces the whole list when updating.")
_TIME_PROPS = {
    "start_time": {**_LOCAL_TIME, "description": "When the workout started (local time in the user's time zone)."},
    "end_time": {**_LOCAL_TIME, "description": "When it ended. Give this or duration_min."},
    "duration_min": {"type": "number", "exclusiveMinimum": 0,
                     "description": "Length in minutes, instead of end_time."},
}

TOOLS: list[tuple[str, dict, Callable[..., str], str]] = [
    ("hevy_workouts", _schema(
        "hevy_workouts",
        "Strength workouts logged in Hevy over a date range, newest first, with each workout's sets, volume and "
        "duration, plus totals for the range. Use for 'what did I train this week' or, with query, to find "
        "workouts with an exercise or title. Default: the last 30 days; at most 366 days.",
        {**_RANGE, "query": _QUERY, "limit": _limit(100, 20), "raw": _RAW}), hevy_workouts, "🏋️"),
    ("hevy_workout", _schema(
        "hevy_workout",
        "One workout in full: every exercise with each set (weight, reps, RPE, set type), notes, the best set and "
        "its estimated 1RM. id is a workout id from hevy_workouts, or 'latest' for the most recent workout.",
        {"id": {"type": "string", "description": "Workout id, or 'latest'."}, "raw": _RAW}, ("id",)),
     hevy_workout, "🏋️"),
    ("hevy_exercises", _schema(
        "hevy_exercises",
        "Search the exercise library (built-in and custom) by name, muscle or equipment. The name may be Hevy's English "
        "title, the name shown in the user's own workouts (any language) or the id. Returns each exercise's id, which "
        "the write tools accept, how often it was done in the last 90 days, when it was last done, and your_name when "
        "the user's workouts call it something else. Use it to find the exact name before logging a workout.",
        {"query": {"type": "string", "description": "Case-insensitive text to match in English titles and in the "
                   "names used in the user's workouts."},
         "muscle": {"type": "string", "enum": list(writes.MUSCLE_GROUPS),
                    "description": "Exercises that work this muscle, as primary or secondary."},
         "equipment": {"type": "string", "enum": list(writes.EQUIPMENT_CATEGORIES)},
         "custom_only": {"type": "boolean", "description": "Only the user's own custom exercises."},
         "limit": _limit(100, 25), "raw": _RAW}), hevy_exercises, "🏋️"),
    ("hevy_exercise_progress", _schema(
        "hevy_exercise_progress",
        "Progress for one exercise over a date range (default one year, at most 1830 days): best estimated 1RM, "
        "heaviest set, trend, stalls, personal records and the last 15 sessions. exercise is a name or id (see "
        "hevy_exercises); an ambiguous name returns the matches so you can pick one.",
        {"exercise": _EXERCISE, **_RANGE, "raw": _RAW}, ("exercise",)), hevy_exercise_progress, "🏋️"),
    ("hevy_records", _schema(
        "hevy_records",
        "Personal records set in the last N days (default 30, at most 366) across all exercises: estimated 1RM, "
        "heaviest weight, most reps in a set, and session volume (volume PRs are opt-in: pass kinds including "
        "'volume'). Newest first, at most 40 rows, with total and truncated when capped. Use for 'what PRs did I hit'.",
        {"days": {**_DAYS, "maximum": MAX_WORKOUT_DAYS},
         "kinds": {"type": "array", "minItems": 1, "uniqueItems": True,
                   "items": {"type": "string", "enum": list(RECORD_KINDS)},
                   "description": "Which PRs to list. Default [\"e1rm\", \"weight\", \"reps\"]; add \"volume\" "
                   "for session-volume PRs."},
         "raw": _RAW}), hevy_records, "🏋️"),
    ("hevy_training_load", _schema(
        "hevy_training_load",
        "Training load for the last N days (default 7, at most 28) against the 28 days before, with sessions, "
        "working sets, volume, sets per muscle group and flags for big changes, missed muscles and push/pull "
        "imbalance. Use for 'am I overdoing it' or 'how was my week'.",
        {"days": {**_DAYS, "maximum": 28}, "raw": _RAW}), hevy_training_load, "🏋️"),
    ("hevy_consistency", _schema(
        "hevy_consistency",
        "Sessions per week over the last N weeks (default 12, at most 52), the current streak, the longest gap "
        "between sessions, the weekdays usually trained and days since the last workout. Use for questions about "
        "habit and frequency.",
        {"weeks": {"type": "integer", "minimum": 2, "maximum": 52, "description": "Default 12."},
         "raw": _RAW}), hevy_consistency, "🏋️"),
    ("hevy_routines", _schema(
        "hevy_routines",
        "The user's saved routines and their folders. Without id: a list of routines. With id: one routine with "
        "its exercises, sets, rep ranges and rest times, which is what to show before editing it with "
        "hevy_save_routine.",
        {"id": {"type": "string", "description": "Routine id. Omit to list all routines."}, "raw": _RAW}),
     hevy_routines, "🏋️"),
    ("hevy_body_measurements", _schema(
        "hevy_body_measurements",
        "Body measurements (weight, body fat and circumferences) logged in Hevy over a date range, default one year, "
        "newest first, with the latest one. Use for weight trends and body composition questions.",
        {**_RANGE, "raw": _RAW}), hevy_body_measurements, "🏋️"),
    ("hevy_log_workout", _schema(
        "hevy_log_workout",
        f"{WRITE_NOTICE} Logs a finished workout. start_time is local time ('YYYY-MM-DD HH:MM'); give end_time or "
        "duration_min. Each exercise is a title or id from hevy_exercises, and each set has its type, weight, "
        "reps and optional RPE.",
        {"title": {"type": "string"}, "description": {"type": "string"},
         **_TIME_PROPS, "is_private": {"type": "boolean", "description": "Default false."},
         "exercises": _WORKOUT_EXERCISES}, ("title", "start_time", "exercises")),
     hevy_log_workout, "✍️"),
    ("hevy_update_workout", _schema(
        "hevy_update_workout",
        f"{WRITE_NOTICE} Edits a logged workout by id; only the fields you pass change. exercises replaces the whole "
        "list, so pass every exercise that should stay. Hevy does not report privacy, so pass is_private: true to "
        "keep a private workout private.",
        {"id": {"type": "string", "description": "Workout id from hevy_workouts."},
         "title": {"type": "string"}, "description": {"type": "string"}, **_TIME_PROPS,
         "is_private": {"type": "boolean"},
         "exercises": _exercises_schema(routine=False, description="Replaces the whole exercise list.")},
        ("id",)), hevy_update_workout, "✍️"),
    ("hevy_save_routine", _schema(
        "hevy_save_routine",
        f"{WRITE_NOTICE} Creates a routine, or updates one when routine_id is given (only the fields you pass change; "
        "exercises replaces the whole list). rest_seconds and rep_range are routine-only. The folder is kept on "
        "update unless folder_id is given.",
        {"routine_id": {"type": "string", "description": "Routine id to update. Omit to create a routine."},
         "title": {"type": "string"}, "folder_id": {"type": "integer", "description": "From hevy_routines."},
         "notes": {"type": "string"}, "exercises": _ROUTINE_EXERCISES}), hevy_save_routine, "✍️"),
    ("hevy_create_routine_folder", _schema(
        "hevy_create_routine_folder",
        f"{WRITE_NOTICE} Creates a routine folder. Its id can be passed as folder_id to hevy_save_routine.",
        {"title": {"type": "string", "description": "Folder name."}}, ("title",)),
     hevy_create_routine_folder, "✍️"),
    ("hevy_create_exercise", _schema(
        "hevy_create_exercise",
        f"{WRITE_NOTICE} Creates a custom exercise in the user's library. It becomes usable in workouts and routines "
        "straight away, and the exercise list is refreshed.",
        {"title": {"type": "string"},
         "exercise_type": {"type": "string", "enum": list(writes.CUSTOM_EXERCISE_TYPES),
                           "description": "What is tracked, such as weight_reps or duration."},
         "equipment_category": {"type": "string", "enum": list(writes.EQUIPMENT_CATEGORIES)},
         "muscle_group": {"type": "string", "enum": list(writes.MUSCLE_GROUPS), "description": "Primary muscle."},
         "other_muscles": {"type": "array", "items": {"type": "string", "enum": list(writes.MUSCLE_GROUPS)}}},
        ("title", "exercise_type", "equipment_category", "muscle_group")), hevy_create_exercise, "✍️"),
    ("hevy_log_body_measurement", _schema(
        "hevy_log_body_measurement",
        f"{WRITE_NOTICE} Saves body measurements for one day (default today). Fields you pass are set, the others keep "
        "their values, and null clears a field. Use for weight, body fat and circumferences.",
        {"date": _DATE, **_measurement_fields()}), hevy_log_body_measurement, "✍️"),
]

WRITE_TOOLS = frozenset({
    "hevy_log_workout",
    "hevy_update_workout",
    "hevy_save_routine",
    "hevy_create_routine_folder",
    "hevy_create_exercise",
    "hevy_log_body_measurement",
})
