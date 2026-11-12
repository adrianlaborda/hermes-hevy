"""Payload builders, merges and human-readable previews for Hevy writes (pure, no I/O).

Model input is validated here and turned into the exact request bodies of the Hevy API
(PostWorkoutsRequestBody, PostRoutinesRequestBody, PutRoutinesRequestBody, PutBodyMeasurement,
CreateCustomExerciseRequestBody). Invalid input raises ``ArgError`` with a message that tells the
model how to fix the call. Weights are always kg on the wire; ``weight_lbs`` is converted here.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timedelta, tzinfo
from typing import Any, Mapping, Optional

from .dates import ArgError, iso_utc, parse_instant, parse_local_time

SET_TYPES = ("normal", "warmup", "failure", "dropset")
RPE = (6, 7, 7.5, 8, 8.5, 9, 9.5, 10)
LB_TO_KG = 0.45359237
PREVIEW_LIMIT = 1500

# Enum lists copied from hevy/openapi.json (CustomExerciseType, MuscleGroup, EquipmentCategory).
CUSTOM_EXERCISE_TYPES = (
    "weight_reps",
    "reps_only",
    "bodyweight_reps",
    "bodyweight_assisted_reps",
    "duration",
    "weight_duration",
    "distance_duration",
    "short_distance_weight",
)
MUSCLE_GROUPS = (
    "abdominals",
    "shoulders",
    "biceps",
    "triceps",
    "forearms",
    "quadriceps",
    "hamstrings",
    "calves",
    "glutes",
    "abductors",
    "adductors",
    "lats",
    "upper_back",
    "traps",
    "lower_back",
    "chest",
    "cardio",
    "neck",
    "full_body",
    "other",
)
EQUIPMENT_CATEGORIES = (
    "none",
    "barbell",
    "dumbbell",
    "kettlebell",
    "machine",
    "plate",
    "resistance_band",
    "suspension",
    "other",
)

# PutBodyMeasurement fields, in OpenAPI order.
MEASUREMENT_FIELDS = (
    "weight_kg",
    "lean_mass_kg",
    "fat_percent",
    "neck_cm",
    "shoulder_cm",
    "chest_cm",
    "left_bicep_cm",
    "right_bicep_cm",
    "left_forearm_cm",
    "right_forearm_cm",
    "abdomen",
    "waist",
    "hips",
    "left_thigh",
    "right_thigh",
    "left_calf",
    "right_calf",
)

# Whole-number set fields shared by workout and routine sets.
_SET_COUNT_FIELDS = ("reps", "distance_meters", "duration_seconds")


# ---------------------------------------------------------------- validation helpers

def _text(value: Any, field: str, *, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise ArgError(f"{field} is required.")
        return None
    if not isinstance(value, str):
        raise ArgError(f"{field} must be text.")
    text = value.strip()
    if not text and required:
        raise ArgError(f"{field} must not be empty.")
    return text or None


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ArgError(f"{field} must be a number, not {value!r}.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ArgError(f"{field} must be a number, not {value!r}.") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise ArgError(f"{field} must be a finite number.")
    return number


def _whole(value: Any, field: str) -> int:
    number = _number(value, field)
    if not number.is_integer():
        raise ArgError(f"{field} must be a whole number, not {value!r}.")
    return int(number)


def _positive_number(value: Any, field: str) -> float:
    number = _number(value, field)
    if number <= 0:
        raise ArgError(f"{field} must be greater than 0, not {value!r}.")
    return number


def _clean_number(value: float) -> float | int:
    return int(value) if float(value).is_integer() else round(value, 2)


def _list(value: Any, field: str, *, required: bool = False) -> list:
    if value is None:
        if required:
            raise ArgError(f"{field} is required.")
        return []
    if not isinstance(value, list):
        raise ArgError(f"{field} must be a list.")
    if required and not value:
        raise ArgError(f"{field} must not be empty.")
    return value


def _choice(value: Any, field: str, allowed: tuple[str, ...]) -> Optional[str]:
    if value is None:
        return None
    if value not in allowed:
        raise ArgError(f"{field} {value!r} is not allowed. Use one of: {', '.join(map(str, allowed))}.")
    return value


def _rpe(value: Any) -> Optional[float]:
    if value is None:
        return None
    number = _number(value, "rpe")
    if number not in RPE:
        raise ArgError(f"rpe {value!r} is not allowed. Use one of: {', '.join(_fmt_num(r) for r in RPE)}.")
    return number


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# ---------------------------------------------------------------- exercises

def resolve_exercise(ref: str, templates: dict, aliases: Optional[Mapping[str, str]] = None) -> dict:
    """The exercise template ``ref`` names.

    Order: exact id; exact template title (any case); exact alias (any case); then a unique substring over
    template titles and alias titles together. ``aliases`` maps a title from the user's own workouts (any
    language) to a template id. An alias match adds ``matched_name`` with the alias text, unless the same
    template was also reached through its English title.
    """
    text = str(ref or "").strip()
    if not text:
        raise ArgError("exercise is required: pass an exercise template id or title from hevy_exercises.")
    if text in templates:
        return dict(templates[text], id=text)
    needle = text.lower()
    named = [(str((t or {}).get("title") or ""), tid, t) for tid, t in templates.items()]
    exact = [(tid, t) for title, tid, t in named if title.lower() == needle]
    if len(exact) == 1:
        return dict(exact[0][1], id=exact[0][0])
    if len(exact) > 1:
        raise ArgError(f"“{text}” matches {len(exact)} exercises ({_labels([(tid, t, None) for tid, t in exact])}). "
                       "Pass the id instead.")
    usable = [(str(name), tid) for name, tid in (aliases or {}).items() if tid in templates and str(name).strip()]
    alias_exact = [(name, tid) for name, tid in usable if name.strip().lower() == needle]
    if alias_exact:
        ids = list(dict.fromkeys(tid for _, tid in alias_exact))
        if len(ids) == 1:
            return _resolved(templates, ids[0], alias_exact[0][0])
        picks = [(tid, templates[tid], next(n for n, t in alias_exact if t == tid)) for tid in ids]
        raise ArgError(f"“{text}” matches {len(ids)} exercises ({_labels(picks)}). Pass the id instead.")
    # Candidate id -> the alias that matched it, or None when its English title matched.
    partial: dict[str, Optional[str]] = {}
    for title, tid, _t in named:
        if needle in title.lower():
            partial[tid] = None
    for name, tid in usable:
        if needle in name.lower():
            partial.setdefault(tid, name)
    if len(partial) == 1:
        tid, alias = next(iter(partial.items()))
        return _resolved(templates, tid, alias)
    if len(partial) > 1:
        picks = [(tid, templates[tid], alias) for tid, alias in partial.items()]
        raise ArgError(f"“{text}” is ambiguous: {_labels(picks)}. Use the exact title or the id.")
    by_name: dict[str, tuple[str, dict, Optional[str]]] = {}
    for title, tid, t in named:
        by_name.setdefault(title.lower(), (tid, t, None))
    for name, tid in usable:
        by_name.setdefault(name.lower(), (tid, templates[tid], name))
    close = difflib.get_close_matches(needle, list(by_name), n=5, cutoff=0.5)
    picks, seen = [], set()
    for c in close:
        tid, t, alias = by_name[c]
        if tid not in seen:
            seen.add(tid)
            picks.append((tid, t, alias))
    if picks:
        raise ArgError(f"Unknown exercise “{text}”. Did you mean: {_labels(picks)}? "
                       "Use the exact title or id; to add a new one, use hevy_create_exercise first.")
    raise ArgError(f"Unknown exercise “{text}”. Find the exact title with hevy_exercises, "
                   "or create a custom exercise with hevy_create_exercise first.")


def _resolved(templates: Mapping[str, dict], template_id: str, alias: Optional[str]) -> dict:
    out = dict(templates[template_id], id=template_id)
    if alias is not None:
        out["matched_name"] = alias
    return out


def _labels(items: list[tuple[str, dict, Optional[str]]]) -> str:
    """``Title (id)``, with ``as “alias”`` when the candidate was reached through the user's own title."""
    parts = []
    for tid, t, alias in items[:8]:
        label = f"{(t or {}).get('title') or tid} ({tid})"
        parts.append(f"{label} as “{alias}”" if alias is not None else label)
    return ", ".join(parts)


def _template_title(templates: Mapping[str, dict], template_id: Any) -> str:
    template = templates.get(str(template_id)) if template_id is not None else None
    return str((template or {}).get("title") or template_id or "Unknown exercise")


def _set_body(s: Mapping[str, Any], *, routine: bool) -> dict:
    """The request fields of one set, all present (None when absent). Used for validated input and GET data."""
    body: dict[str, Any] = {
        "type": s.get("type") or "normal",
        "weight_kg": s.get("weight_kg"),
        "reps": s.get("reps"),
        "distance_meters": s.get("distance_meters"),
        "duration_seconds": s.get("duration_seconds"),
        "custom_metric": s.get("custom_metric"),
    }
    if routine:
        rep_range = s.get("rep_range")
        body["rep_range"] = (
            {"start": rep_range.get("start"), "end": rep_range.get("end")} if rep_range else None
        )
    else:
        body["rpe"] = s.get("rpe")
    return body


def normalize_set(s: dict, *, routine: bool) -> dict:
    """One validated set in the request shape of a workout (``routine=False``) or a routine."""
    if not isinstance(s, dict):
        raise ArgError("Each set must be an object such as {\"reps\": 5, \"weight_kg\": 100}.")
    set_type = _choice(s.get("type") or "normal", "set type", SET_TYPES)
    if s.get("weight_kg") is not None and s.get("weight_lbs") is not None:
        raise ArgError("Give weight_kg or weight_lbs in a set, not both.")
    if s.get("weight_lbs") is not None:
        weight = _clean_number(_number(s["weight_lbs"], "weight_lbs") * LB_TO_KG)
        if weight < 0:
            raise ArgError("weight_lbs must not be negative.")
    elif s.get("weight_kg") is not None:
        weight = _clean_number(_number(s["weight_kg"], "weight_kg"))
        if weight < 0:
            raise ArgError("weight_kg must not be negative.")
    else:
        weight = None
    out: dict[str, Any] = {"type": set_type, "weight_kg": weight}
    for field in _SET_COUNT_FIELDS:
        value = s.get(field)
        if value is not None:
            count = _whole(value, field)
            if count < 0:
                raise ArgError(f"{field} must not be negative.")
            value = count
        out[field] = value
    out["custom_metric"] = None if s.get("custom_metric") is None else _clean_number(
        _number(s["custom_metric"], "custom_metric"))
    if routine:
        if s.get("rpe") is not None:
            raise ArgError("rpe is only allowed on logged workout sets, not in routines.")
        rep_range = s.get("rep_range")
        if rep_range is not None:
            if not isinstance(rep_range, dict) or rep_range.get("start") is None or rep_range.get("end") is None:
                raise ArgError("rep_range must be {\"start\": 8, \"end\": 12}.")
            start, end = _whole(rep_range["start"], "rep_range.start"), _whole(rep_range["end"], "rep_range.end")
            if start < 1 or end < start:
                raise ArgError(f"rep_range {start}-{end} is invalid: start must be >= 1 and not after end.")
            out["rep_range"] = {"start": start, "end": end}
        else:
            out["rep_range"] = None
    else:
        if s.get("rep_range") is not None:
            raise ArgError("rep_range is only allowed in routines, not in logged workouts.")
        out["rpe"] = _rpe(s.get("rpe"))
    return out


def _sets(exercise: dict, *, routine: bool, label: str) -> list[dict]:
    sets = _list(exercise.get("sets"), f"{label} sets", required=True)
    return [normalize_set(s, routine=routine) for s in sets]


def _exercise_body(entry: Any, templates: dict, *, routine: bool, index: int,
                   aliases: Optional[Mapping[str, str]] = None) -> dict:
    if not isinstance(entry, dict):
        raise ArgError(f"exercises[{index}] must be an object with exercise and sets.")
    label = f"exercises[{index}]"
    template = resolve_exercise(entry.get("exercise"), templates, aliases)
    superset = entry.get("superset_id")
    body: dict[str, Any] = {
        "exercise_template_id": template["id"],
        "superset_id": None if superset is None else _whole(superset, f"{label}.superset_id"),
        "notes": _text(entry.get("notes"), f"{label}.notes"),
        "sets": _sets(entry, routine=routine, label=label),
    }
    if routine:
        rest = entry.get("rest_seconds")
        body["rest_seconds"] = None if rest is None else _whole(rest, f"{label}.rest_seconds")
    return body


def _exercises(entries: Any, templates: dict, *, routine: bool,
               aliases: Optional[Mapping[str, str]] = None) -> list[dict]:
    items = _list(entries, "exercises", required=True)
    return [_exercise_body(e, templates, routine=routine, index=i, aliases=aliases) for i, e in enumerate(items)]


# ---------------------------------------------------------------- workouts

def _workout_times(args: Mapping[str, Any], tz: tzinfo, current: Optional[tuple[datetime, datetime]] = None
                   ) -> tuple[datetime, datetime]:
    """(start, end) as aware datetimes from a model call, falling back to ``current`` for omitted values."""
    if args.get("start_time") is not None:
        start = parse_local_time(args["start_time"], tz)
    elif current is not None:
        start = current[0]
    else:
        raise ArgError("start_time is required: 'YYYY-MM-DD HH:MM' in the user's local time.")
    if args.get("end_time") is not None and args.get("duration_min") is not None:
        raise ArgError("Give end_time or duration_min, not both.")
    if args.get("end_time") is not None:
        end = parse_local_time(args["end_time"], tz)
    elif args.get("duration_min") is not None:
        minutes = _positive_number(args["duration_min"], "duration_min")
        end = start + timedelta(minutes=minutes)
    elif current is not None and args.get("start_time") is not None:
        end = start + (current[1] - current[0])  # keep the length when only the start moves
    elif current is not None:
        end = current[1]
    else:
        raise ArgError("Give end_time or duration_min so the workout has a length.")
    if end <= start:
        raise ArgError(f"end_time must be after start_time ({start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}).")
    return start, end


def build_workout(args: dict, templates: dict, tz: tzinfo, *, aliases: Optional[Mapping[str, str]] = None) -> dict:
    """``{"workout": ...}`` as PostWorkoutsRequestBody, with local times converted to UTC ``Z`` strings."""
    title = _text(args.get("title"), "title", required=True)
    start, end = _workout_times(args, tz)
    return {"workout": {
        "title": title,
        "description": _text(args.get("description"), "description"),
        "start_time": iso_utc(start),
        "end_time": iso_utc(end),
        "is_private": _bool(args.get("is_private"), "is_private"),
        "exercises": _exercises(args.get("exercises"), templates, routine=False, aliases=aliases),
    }}


def _bool(value: Any, field: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ArgError(f"{field} must be true or false.")
    return value


def workout_to_request(workout: dict) -> dict:
    """A GET Workout as a PostWorkoutsRequestBody (ids, indexes and titles of exercises dropped)."""
    exercises = []
    for e in workout.get("exercises") or []:
        superset = e.get("superset_id")
        exercises.append({
            "exercise_template_id": e.get("exercise_template_id"),
            "superset_id": superset,
            "notes": e.get("notes"),
            "sets": [_set_body(s, routine=False) for s in e.get("sets") or []],
        })
    return {"workout": {
        "title": workout.get("title"),
        "description": workout.get("description"),
        "start_time": workout.get("start_time"),
        "end_time": workout.get("end_time"),
        "is_private": bool(workout.get("is_private", False)),
        "exercises": exercises,
    }}


def apply_workout_changes(current: dict, args: dict, templates: dict, tz: tzinfo, *,
                          aliases: Optional[Mapping[str, str]] = None) -> dict:
    """``current`` (from workout_to_request) with the fields in ``args`` replaced; the rest is kept."""
    body = dict(current["workout"])
    if args.get("title") is not None:
        body["title"] = _text(args["title"], "title", required=True)
    if "description" in args:
        body["description"] = _text(args["description"], "description")
    if args.get("is_private") is not None:
        body["is_private"] = _bool(args["is_private"], "is_private")
    if any(args.get(k) is not None for k in ("start_time", "end_time", "duration_min")):
        current_times = (parse_instant(body.get("start_time")), parse_instant(body.get("end_time")))
        if current_times[0] is None or current_times[1] is None:
            current_times = None
        start, end = _workout_times(args, tz, current=current_times)
        body["start_time"], body["end_time"] = iso_utc(start), iso_utc(end)
    if args.get("exercises") is not None:
        body["exercises"] = _exercises(args["exercises"], templates, routine=False, aliases=aliases)
    return {"workout": body}


def describe_workout(body: dict, templates: dict, tz: tzinfo, *, action: str = "Log") -> str:
    """Plain text for the approval dialog: the action and the workout, then one line per exercise."""
    workout = body["workout"]
    start = parse_instant(workout.get("start_time"))
    end = parse_instant(workout.get("end_time"))
    when = "at an unknown time"
    if start is not None:
        local_start = start.astimezone(tz)
        when = local_start.strftime("on %a %Y-%m-%d %H:%M")
        if end is not None:
            local_end = end.astimezone(tz)
            end_text = local_end.strftime("%H:%M" if local_end.date() == local_start.date() else "%a %Y-%m-%d %H:%M")
            minutes = round((end - start).total_seconds() / 60)
            when += f"–{end_text} ({minutes} min)"
    lines = [f"{action} workout “{workout.get('title') or 'Untitled'}” {when}, "
             f"private: {'yes' if workout.get('is_private') else 'no'}"]
    for e in workout.get("exercises") or []:
        sets = ", ".join(_set_text(s, routine=False) for s in e.get("sets") or []) or "no sets"
        lines.append(f"- {_template_title(templates, e.get('exercise_template_id'))}: {sets}")
    return _cap("\n".join(lines))


# ---------------------------------------------------------------- routines

def build_routine(args: dict, templates: dict, *, aliases: Optional[Mapping[str, str]] = None) -> dict:
    """``{"routine": ...}`` as PostRoutinesRequestBody."""
    folder = args.get("folder_id")
    return {"routine": {
        "title": _text(args.get("title"), "title", required=True),
        "folder_id": None if folder is None else _whole(folder, "folder_id"),
        "notes": _text(args.get("notes"), "notes"),
        "exercises": _exercises(args.get("exercises"), templates, routine=True, aliases=aliases),
    }}


def routine_to_request(routine: dict) -> dict:
    """A GET Routine as a PutRoutinesRequestBody. folder_id is dropped: a PUT keeps the folder unless asked."""
    exercises = []
    for e in routine.get("exercises") or []:
        exercises.append({
            "exercise_template_id": e.get("exercise_template_id"),
            "superset_id": e.get("superset_id"),
            "rest_seconds": e.get("rest_seconds"),
            "notes": e.get("notes"),
            "sets": [_set_body(s, routine=True) for s in e.get("sets") or []],
        })
    return {"routine": {
        "title": routine.get("title"),
        "notes": routine.get("notes"),
        "exercises": exercises,
    }}


def apply_routine_changes(current: dict, args: dict, templates: dict, *,
                          aliases: Optional[Mapping[str, str]] = None) -> dict:
    """``current`` (from routine_to_request) with the fields in ``args`` replaced; folder_id only when given."""
    body = dict(current["routine"])
    if args.get("title") is not None:
        body["title"] = _text(args["title"], "title", required=True)
    if "notes" in args:
        body["notes"] = _text(args["notes"], "notes")
    if "folder_id" in args:
        folder = args["folder_id"]
        body["folder_id"] = None if folder is None else _whole(folder, "folder_id")
    if args.get("exercises") is not None:
        body["exercises"] = _exercises(args["exercises"], templates, routine=True, aliases=aliases)
    return {"routine": body}


def describe_routine(body: dict, templates: dict, *, action: str = "Create") -> str:
    routine = body["routine"]
    exercises = routine.get("exercises") or []
    folder = routine.get("folder_id")
    where = "" if folder is None else f", folder {folder}"
    lines = [f"{action} routine “{routine.get('title') or 'Untitled'}” "
             f"({len(exercises)} exercise{'s' if len(exercises) != 1 else ''}{where})"]
    for e in exercises:
        sets = ", ".join(_set_text(s, routine=True) for s in e.get("sets") or []) or "no sets"
        rest = f", rest {e['rest_seconds']} s" if e.get("rest_seconds") is not None else ""
        lines.append(f"- {_template_title(templates, e.get('exercise_template_id'))}: {sets}{rest}")
    return _cap("\n".join(lines))


# ---------------------------------------------------------------- measurements

def merge_measurement(existing: Optional[dict], changes: dict) -> dict:
    """The full PutBodyMeasurement body: existing values kept, changed ones replaced, explicit None clears."""
    unknown = sorted(k for k in changes if k not in MEASUREMENT_FIELDS)
    if unknown:
        raise ArgError(f"Unknown measurement {', '.join(map(repr, unknown))}. "
                       f"Use these keys: {', '.join(MEASUREMENT_FIELDS)}.")
    base = existing or {}
    body: dict[str, Any] = {}
    for field in MEASUREMENT_FIELDS:
        value = changes[field] if field in changes else base.get(field)
        if value is not None:
            value = _clean_number(_positive_number(value, field))
        body[field] = value
    return body


def describe_measurement(day: str, body: dict, existing: Optional[dict]) -> str:
    """Only the fields whose value changes, as ``field old → new``."""
    base = existing or {}
    changed = [f for f in MEASUREMENT_FIELDS if body.get(f) != base.get(f)]
    lines = [f"Save body measurement for {day}"]
    if not changed:
        lines.append("no changes")
    for field in changed:
        old = _dash(base.get(field))
        lines.append(f"{field} {old} → {_dash(body.get(field))}")
    return _cap("\n".join(lines))


def _dash(value: Any) -> str:
    return "–" if value is None else _fmt_num(value) if isinstance(value, (int, float)) else str(value)


# ---------------------------------------------------------------- custom exercises

def build_exercise_template(args: dict) -> dict:
    """``{"exercise": ...}`` as CreateCustomExerciseRequestBody. Enum values are checked against the API lists."""
    others = _list(args.get("other_muscles"), "other_muscles")
    return {"exercise": {
        "title": _text(args.get("title"), "title", required=True),
        "exercise_type": _required_choice(args, "exercise_type", CUSTOM_EXERCISE_TYPES),
        "equipment_category": _required_choice(args, "equipment_category", EQUIPMENT_CATEGORIES),
        "muscle_group": _required_choice(args, "muscle_group", MUSCLE_GROUPS),
        "other_muscles": [_choice(m, "other_muscles", MUSCLE_GROUPS) for m in others],
    }}


def _required_choice(args: Mapping[str, Any], field: str, allowed: tuple[str, ...]) -> str:
    if args.get(field) is None:
        raise ArgError(f"{field} is required. Use one of: {', '.join(allowed)}.")
    return _choice(args[field], field, allowed)


def describe_exercise_template(body: dict) -> str:
    exercise = body["exercise"]
    details = [exercise.get(k) for k in ("exercise_type", "equipment_category", "muscle_group")]
    others = exercise.get("other_muscles") or []
    if others:
        details.append("also " + ", ".join(others))
    shown = ", ".join(d for d in details if d) or "no details"
    return _cap(f"Create custom exercise “{exercise.get('title')}” ({shown})")


# ---------------------------------------------------------------- shared preview helpers

def _set_text(s: dict, *, routine: bool) -> str:
    text = ""
    weight = s.get("weight_kg")
    if weight is not None:
        text = f"{_fmt_num(weight)} kg"
    rep_range = s.get("rep_range") if routine else None
    reps = f"{rep_range['start']}-{rep_range['end']}" if rep_range else (
        None if s.get("reps") is None else _fmt_num(s["reps"]))
    if reps is not None:
        text = f"{text} x {reps}" if text else f"x {reps}"
    if s.get("distance_meters") is not None:
        text = (text + " " if text else "") + f"{_fmt_num(s['distance_meters'] / 1000)} km"
    if s.get("duration_seconds") is not None:
        text = (text + " " if text else "") + f"{_fmt_num(s['duration_seconds'])} s"
    if not text:
        text = "set"
    if s.get("type") not in (None, "normal"):
        text = f"{s['type']} {text}"
    if not routine and s.get("rpe") is not None:
        text += f" (RPE {_fmt_num(s['rpe'])})"
    return text


def _cap(text: str) -> str:
    return text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT - 1] + "…"
