"""Per-workout and per-exercise views of Hevy data as compact, model-friendly dicts. Pure, no I/O.

Only working sets (normal, failure, dropset) count toward volume, reps and e1RM; warm-ups stay visible
in the detail views but are never counted. Dates are the user's local calendar days in ``tz``; weights
are kg. Keys whose value would be None are dropped only where the design says so.
"""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from typing import Any, Iterable, Mapping, Optional

from .dates import parse_instant

WORKING = frozenset({"normal", "failure", "dropset"})

_MAX_E1RM_REPS = 12  # Epley estimates stop being meaningful beyond this
_TREND_DAYS = 28  # the slope is reported per four weeks
_TREND_SESSIONS = 6  # change_pct needs two blocks of three sessions
_STALL_SESSIONS = 4
_STALL_DAYS = 21


# -- numbers -----------------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _get(s: Mapping[str, Any], key: str) -> float:
    return _num(s.get(key)) or 0.0


def _fmt(value: float) -> str:
    """``100.0`` -> ``"100"``, ``62.5`` -> ``"62.5"``."""
    return f"{round(value, 2):g}"


def hmm(seconds: Any) -> Optional[str]:
    """Seconds as ``h:mm`` (``3900`` -> ``"1:05"``)."""
    value = _num(seconds)
    if value is None or value < 0:
        return None
    minutes = int(round(value / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"


# -- sets --------------------------------------------------------------------------------------

def is_working(s: dict) -> bool:
    return s.get("type") in WORKING


def set_volume_kg(s: dict) -> float:
    if not is_working(s):
        return 0.0
    weight, reps = _get(s, "weight_kg"), _get(s, "reps")
    if weight <= 0 or reps <= 0:
        return 0.0
    return weight * reps


def e1rm(weight_kg: Any, reps: Any) -> Optional[float]:
    """Epley estimate of the one-rep max. Only 1 to 12 reps with a positive weight qualify."""
    weight, count = _num(weight_kg), _num(reps)
    if weight is None or count is None or weight <= 0 or count < 1 or count > _MAX_E1RM_REPS:
        return None
    if count == 1:
        return weight
    return weight * (1 + count / 30)


def compact_set(s: dict) -> dict:
    out = {
        "type": s.get("type"),
        "weight_kg": s.get("weight_kg"),
        "reps": s.get("reps"),
        "rpe": s.get("rpe"),
        "distance_m": s.get("distance_meters"),
        "duration_s": s.get("duration_seconds"),
        "custom_metric": s.get("custom_metric"),
    }
    out = {key: value for key, value in out.items() if value is not None}
    if out.get("type") == "normal":
        del out["type"]
    return out


def set_label(s: dict) -> str:
    """``"100 kg x 5"``, ``"x 12"``, ``"45 s"``, ``"1.2 km"``; ``" + RPE 8"`` appended when logged."""
    weight, reps = _get(s, "weight_kg"), _get(s, "reps")
    distance, duration = _get(s, "distance_meters"), _get(s, "duration_seconds")
    parts = []
    if weight > 0:
        parts.append(f"{_fmt(weight)} kg" + (f" x {_fmt(reps)}" if reps else ""))
    elif reps:
        parts.append(f"x {_fmt(reps)}")
    if distance:
        parts.append(f"{_fmt(distance / 1000)} km" if distance >= 1000 else f"{_fmt(distance)} m")
    if duration:
        parts.append(f"{_fmt(duration)} s")
    text = ", ".join(parts)
    rpe = _num(s.get("rpe"))
    if rpe is not None:
        text = f"{text} + RPE {_fmt(rpe)}" if text else f"RPE {_fmt(rpe)}"
    return text


def _heaviest(sets: list[dict]) -> Optional[dict]:
    weighted = [s for s in sets if _get(s, "weight_kg") > 0]
    if not weighted:
        return None
    return max(weighted, key=lambda s: (_get(s, "weight_kg"), _get(s, "reps")))


def _best_set(sets: list[dict]) -> Optional[dict]:
    """Highest e1RM, else the heaviest, else the most reps."""
    scored = [(e1rm(s.get("weight_kg"), s.get("reps")), s) for s in sets]
    scored = [(value, s) for value, s in scored if value is not None]
    if scored:
        return max(scored, key=lambda pair: pair[0])[1]
    heaviest = _heaviest(sets)
    if heaviest is not None:
        return heaviest
    counted = [s for s in sets if _get(s, "reps") > 0]
    return max(counted, key=lambda s: _get(s, "reps")) if counted else None


def _working_sets(workout: dict) -> list[dict]:
    return [s for ex in workout.get("exercises") or [] for s in ex.get("sets") or [] if is_working(s)]


def _muscle(template_id: Any, templates: Optional[dict]) -> str:
    template = (templates or {}).get(template_id) or {}
    return template.get("primary_muscle_group") or "other"


def _hhmm(moment: Optional[datetime], tz: tzinfo) -> Optional[str]:
    return moment.astimezone(tz).strftime("%H:%M") if moment is not None else None


# -- workouts ----------------------------------------------------------------------------------

def workout_summary(w: dict, tz: tzinfo, templates: Optional[dict] = None) -> dict:
    start = parse_instant(w.get("start_time"))
    end = parse_instant(w.get("end_time"))
    exercises = w.get("exercises") or []
    working = _working_sets(w)
    seconds = (end - start).total_seconds() if start is not None and end is not None else None
    out: dict[str, Any] = {
        "id": w.get("id"),
        "title": w.get("title") or "",
        "date": start.astimezone(tz).date().isoformat() if start is not None else None,
        "start_local": _hhmm(start, tz),
        "end_local": _hhmm(end, tz),
        "duration_hmm": hmm(seconds),
        "duration_min": round(seconds / 60) if seconds is not None else None,
        "exercises": len(exercises),
        "working_sets": len(working),
        "reps": sum(int(_get(s, "reps")) for s in working),
        "volume_kg": round(sum(set_volume_kg(s) for s in working)),
    }
    if w.get("routine_id"):
        out["routine_id"] = w["routine_id"]
    out["exercise_titles"] = [ex.get("title") or "" for ex in exercises[:6]]
    if templates is not None:
        counts: dict[str, int] = {}
        for ex in exercises:
            n = sum(1 for s in ex.get("sets") or [] if is_working(s))
            if n:
                muscle = _muscle(ex.get("exercise_template_id"), templates)
                counts[muscle] = counts.get(muscle, 0) + n
        out["sets_by_muscle"] = counts
    return out


def workout_detail(w: dict, tz: tzinfo, templates: Optional[dict] = None) -> dict:
    out = workout_summary(w, tz, templates)
    if w.get("description"):
        out["description"] = w["description"]
    out["exercises"] = [_exercise_detail(ex, templates) for ex in w.get("exercises") or []]
    return out


def _exercise_detail(ex: dict, templates: Optional[dict]) -> dict:
    sets = ex.get("sets") or []
    working = [s for s in sets if is_working(s)]
    template_id = ex.get("exercise_template_id")
    row: dict[str, Any] = {"title": ex.get("title") or "", "exercise_template_id": template_id}
    if ex.get("notes"):
        row["notes"] = ex["notes"]
    if ex.get("superset_id") is not None:
        row["superset_id"] = ex["superset_id"]
    if templates is not None:
        row["muscle"] = _muscle(template_id, templates)
    row["sets"] = [compact_set(s) for s in sets]
    row["working_sets"] = len(working)
    row["volume_kg"] = round(sum(set_volume_kg(s) for s in working))
    best = _best_set(working)
    row["best_set"] = set_label(best) if best is not None else None
    e1s = [value for value in (e1rm(s.get("weight_kg"), s.get("reps")) for s in working) if value is not None]
    if e1s:
        row["best_e1rm_kg"] = round(max(e1s), 1)
    return row


# -- exercises across workouts -----------------------------------------------------------------

def _positive_max(sets: list[dict], key: str) -> Optional[float]:
    values = [_get(s, key) for s in sets if _get(s, key) > 0]
    return max(values) if values else None


def _session_row(w: dict, start: datetime, tz: tzinfo, entries: list[dict]) -> dict:
    """One exercise's working sets in one workout; several entries of the same exercise are merged."""
    working = [s for ex in entries for s in ex.get("sets") or [] if is_working(s)]
    e1s = [value for value in (e1rm(s.get("weight_kg"), s.get("reps")) for s in working) if value is not None]
    heaviest = _heaviest(working)
    reps = [int(_get(s, "reps")) for s in working if _get(s, "reps") > 0]
    row: dict[str, Any] = {
        "date": start.astimezone(tz).date().isoformat(),
        "workout_id": w.get("id"),
        "workout_title": w.get("title") or "",
        "working_sets": len(working),
        "reps": sum(int(_get(s, "reps")) for s in working),
        "volume_kg": round(sum(set_volume_kg(s) for s in working)),
        "top_weight_kg": _get(heaviest, "weight_kg") if heaviest is not None else None,
        "top_set": set_label(heaviest) if heaviest is not None else None,
        "best_e1rm_kg": round(max(e1s), 1) if e1s else None,
        "best_reps": max(reps) if reps else None,
        "best_duration_s": _positive_max(working, "duration_seconds"),
        "best_distance_m": _positive_max(working, "distance_meters"),
    }
    return {key: value for key, value in row.items() if value is not None}


def _group_sessions(workouts: Iterable[dict], tz: tzinfo) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Session rows per exercise template id, oldest first, plus each exercise's latest title."""
    timed = []
    for w in workouts:
        start = parse_instant(w.get("start_time"))
        if start is not None:
            timed.append((start, w))
    timed.sort(key=lambda pair: pair[0])
    sessions: dict[str, list[dict]] = {}
    titles: dict[str, str] = {}
    for start, w in timed:
        grouped: dict[str, list[dict]] = {}
        for ex in w.get("exercises") or []:
            template_id = ex.get("exercise_template_id")
            if template_id:
                grouped.setdefault(template_id, []).append(ex)
        for template_id, entries in grouped.items():
            sessions.setdefault(template_id, []).append(_session_row(w, start, tz, entries))
            titles[template_id] = entries[0].get("title") or titles.get(template_id, "")
    return sessions, titles


def exercise_sessions(workouts: list[dict], template_id: str, tz: tzinfo) -> list[dict]:
    sessions, _ = _group_sessions(workouts, tz)
    return sessions.get(template_id, [])


def _pr_events(sessions: list[dict], *, volume: bool) -> list[dict]:
    """Sessions that beat every earlier session on a kind. The first session never counts.

    Reps count only for exercises that were never loaded with weight (bodyweight work)."""
    weighted_ever = any(s.get("top_weight_kg") is not None for s in sessions)
    kinds = [("e1rm", "best_e1rm_kg", "kg"), ("weight", "top_weight_kg", "kg")]
    if not weighted_ever:
        kinds.append(("reps", "best_reps", "reps"))
    if volume:
        kinds.append(("volume", "volume_kg", "kg"))
    events = []
    for index in range(1, len(sessions)):
        current = sessions[index]
        for kind, key, unit in kinds:
            value = current.get(key)
            past = [s[key] for s in sessions[:index] if s.get(key) is not None]
            if value is None or not past:
                continue
            previous = max(past)
            if value > previous:
                events.append({
                    "date": current["date"], "kind": kind, "value": value, "previous": previous,
                    "unit": unit, "workout_id": current.get("workout_id"),
                })
    return events


def _slope_per_4_weeks(e1s: list[dict], origin: Optional[date]) -> Optional[float]:
    """Least-squares slope of best e1RM against days since the first session, per four weeks."""
    if origin is None or len(e1s) < 2:
        return None
    points = [((date.fromisoformat(s["date"]) - origin).days, s["best_e1rm_kg"]) for s in e1s]
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    if spread == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
    return round(slope * _TREND_DAYS, 1)


def _change_pct(e1s: list[dict]) -> Optional[float]:
    values = [s["best_e1rm_kg"] for s in e1s]
    if len(values) < _TREND_SESSIONS:
        return None
    recent = sum(values[-3:]) / 3
    prior = sum(values[-6:-3]) / 3
    if prior <= 0:
        return None
    return round((recent - prior) / prior * 100, 1)


def _stalled(e1s: list[dict], today: date) -> bool:
    """The last four sessions did not beat the best e1RM set before them, and that best is 21+ days old."""
    if len(e1s) < _STALL_SESSIONS:
        return False
    head, tail = e1s[:-_STALL_SESSIONS], e1s[-_STALL_SESSIONS:]
    if not head:
        return False
    best = max(head, key=lambda s: s["best_e1rm_kg"])
    if max(s["best_e1rm_kg"] for s in tail) > best["best_e1rm_kg"]:
        return False
    return (today - date.fromisoformat(best["date"])).days >= _STALL_DAYS


def progress(sessions: list[dict], *, today: date) -> dict:
    last = sessions[-1]["date"] if sessions else None
    e1s = [s for s in sessions if s.get("best_e1rm_kg") is not None]
    weighted = [s for s in sessions if s.get("top_weight_kg") is not None]
    best_e1rm = None
    if e1s:
        top = max(e1s, key=lambda s: s["best_e1rm_kg"])
        best_e1rm = {"kg": top["best_e1rm_kg"], "date": top["date"]}
    best_weight = None
    if weighted:
        top = max(weighted, key=lambda s: s["top_weight_kg"])
        best_weight = {"kg": top["top_weight_kg"], "date": top["date"], "set": top.get("top_set")}
    origin = date.fromisoformat(sessions[0]["date"]) if sessions else None
    prs = [
        {key: event[key] for key in ("date", "kind", "value", "previous")}
        for event in _pr_events(sessions, volume=False)
    ]
    return {
        "sessions": len(sessions),
        "first_date": sessions[0]["date"] if sessions else None,
        "last_date": last,
        "days_since_last": (today - date.fromisoformat(last)).days if last else None,
        "best_e1rm": best_e1rm,
        "best_weight": best_weight,
        "e1rm_trend": {
            "slope_kg_per_4_weeks": _slope_per_4_weeks(e1s, origin),
            "change_pct": _change_pct(e1s),
        },
        "stalled": _stalled(e1s, today),
        "prs": prs,
    }


def personal_records(workouts: list[dict], tz: tzinfo, *, since: date, templates: Optional[dict] = None) -> list[dict]:
    """Every PR dated on or after ``since`` across all workouts, newest first."""
    sessions_by_id, titles = _group_sessions(workouts, tz)
    since_iso = since.isoformat()
    rows = []
    for template_id, sessions in sessions_by_id.items():
        template = (templates or {}).get(template_id) or {}
        name = template.get("title") or titles.get(template_id) or template_id
        for event in _pr_events(sessions, volume=True):
            if event["date"] < since_iso:
                continue
            rows.append({
                "date": event["date"],
                "exercise": name,
                "exercise_template_id": template_id,
                "kind": event["kind"],
                "value": event["value"],
                "previous": event["previous"],
                "unit": event["unit"],
                "workout_id": event["workout_id"],
            })
    rows.sort(key=lambda row: row["date"], reverse=True)
    return rows
