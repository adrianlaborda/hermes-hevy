"""Muscle balance, weekly load and training consistency. Pure functions over Hevy workouts.

Working sets are the sets Hevy does not mark as warm-ups. A primary muscle gets one set per working set
and each secondary muscle half a set, so an exercise that trains triceps as well as chest counts for both.
Windows are local calendar days in the user's zone. The baseline is the period before the window, scaled
to the window's length, so a 7-day week and a 28-day baseline compare as rates.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta, tzinfo
from typing import Any, Mapping, Optional

from .dates import parse_instant

PUSH = ("chest", "shoulders", "triceps")
PULL = ("lats", "upper_back", "biceps")
WORKING = frozenset({"normal", "failure", "dropset"})
OTHER = "other"
SECONDARY_WEIGHT = 0.5
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Flag thresholds. Baselines are per-window rates, so the minimums are per week for a 7-day window.
RATIO_HIGH = 1.3
RATIO_LOW = 0.7
MIN_BASE_SETS = 10.0
MIN_BASE_VOLUME_KG = 1000.0
MUSCLE_RATIO_HIGH = 1.5
MIN_BASE_MUSCLE_SETS = 3.0
MISSED_BASE_SETS = 4.0
PUSH_PULL_MIN_SETS = 6
PUSH_PULL_HIGH = 1.5
PUSH_PULL_LOW = 0.67
USUAL_SHARE = 0.4


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _is_working(s: Any) -> bool:
    return isinstance(s, Mapping) and s.get("type") in WORKING


def _set_volume(s: Mapping[str, Any]) -> float:
    weight, reps = _num(s.get("weight_kg")), _num(s.get("reps"))
    return weight * reps if weight is not None and reps is not None and weight > 0 and reps > 0 else 0.0


def _working_sets(workout: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for e in workout.get("exercises") or [] if isinstance(e, Mapping)
            for s in e.get("sets") or [] if _is_working(s)]


def _local_day(workout: Mapping[str, Any], tz: tzinfo) -> Optional[date]:
    start = parse_instant(workout.get("start_time"))
    return start.astimezone(tz).date() if start else None


def _duration_min(workout: Mapping[str, Any]) -> float:
    start, end = parse_instant(workout.get("start_time")), parse_instant(workout.get("end_time"))
    return (end - start).total_seconds() / 60 if start and end and end > start else 0.0


def _muscle_sets(workouts: list, templates: Mapping[str, Mapping[str, Any]],
                 secondary_weight: float = SECONDARY_WEIGHT) -> dict[str, float]:
    """Unrounded working sets per muscle; a template missing from ``templates`` counts as ``other``."""
    counts: dict[str, float] = defaultdict(float)
    for workout in workouts:
        for exercise in workout.get("exercises") or []:
            if not isinstance(exercise, Mapping):
                continue
            n = sum(1 for s in exercise.get("sets") or [] if _is_working(s))
            if not n:
                continue
            template = templates.get(exercise.get("exercise_template_id"))
            primary, secondary = OTHER, []
            if isinstance(template, Mapping):
                primary = template.get("primary_muscle_group") or OTHER
                secondary = [m for m in template.get("secondary_muscle_groups") or [] if m and m != primary]
            counts[primary] += n
            for muscle in secondary:
                counts[muscle] += secondary_weight * n
    return counts


def _totals(workouts: list) -> dict[str, float]:
    return {
        "sessions": float(len(workouts)),
        "working_sets": float(sum(len(_working_sets(w)) for w in workouts)),
        "volume_kg": sum(_set_volume(s) for w in workouts for s in _working_sets(w)),
        "duration_min": sum(_duration_min(w) for w in workouts),
    }


def _fmt(value: float) -> str:
    return f"{round(value, 1):g}"


def sets_by_muscle(workouts, templates, *, secondary_weight: float = SECONDARY_WEIGHT) -> dict[str, float]:
    """Working sets per muscle group: primary +1 per set, each secondary +``secondary_weight``.
    Sets of an exercise whose template is unknown are counted under ``other``."""
    counts = _muscle_sets(list(workouts or []), templates or {}, secondary_weight)
    return {m: round(v, 1) for m, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}


def _base_label(baseline_days: int) -> str:
    return f"{baseline_days // 7}-week" if baseline_days % 7 == 0 else f"{baseline_days}-day"


def training_load(workouts, templates, tz, *, today: date, days: int = 7, baseline_days: int = 28) -> dict:
    """Load over the ``days`` local days ending today, against the ``baseline_days`` before them scaled to
    the same length, with deterministic flags (thresholds above)."""
    window_start = today - timedelta(days=days - 1)
    base_end = window_start - timedelta(days=1)
    base_start = window_start - timedelta(days=baseline_days)
    scale = days / baseline_days
    period = "this week" if days == 7 else f"in the last {days} days"
    per = "per week" if days == 7 else f"per {days} days"
    base_label = _base_label(baseline_days)

    in_window, in_base = [], []
    for workout in workouts or []:
        day = _local_day(workout, tz)
        if day is None:
            continue
        if window_start <= day <= today:
            in_window.append(workout)
        elif base_start <= day <= base_end:
            in_base.append(workout)

    win, raw_base = _totals(in_window), _totals(in_base)
    base = {k: v * scale for k, v in raw_base.items()}
    templates = templates or {}
    win_muscle = _muscle_sets(in_window, templates)
    base_muscle = {m: v * scale for m, v in _muscle_sets(in_base, templates).items()}

    muscles = sorted(set(win_muscle) | set(base_muscle), key=lambda m: (-win_muscle.get(m, 0.0), m))
    by_muscle = []
    for m in muscles:
        now, before = win_muscle.get(m, 0.0), base_muscle.get(m, 0.0)
        by_muscle.append({"muscle": m, "sets": round(now, 1), "baseline_sets": round(before, 1),
                          "ratio": round(now / before, 2) if before > 0 else None})

    push = sum(win_muscle.get(m, 0.0) for m in PUSH)
    pull = sum(win_muscle.get(m, 0.0) for m in PULL)
    push_pull = {"push_sets": round(push, 1), "pull_sets": round(pull, 1),
                 "ratio": round(push / pull, 2) if pull > 0 else None}

    flags: list[dict] = []
    ws, bws = win["working_sets"], base["working_sets"]
    if bws >= MIN_BASE_SETS:
        ratio = ws / bws
        if ratio >= RATIO_HIGH or ratio <= RATIO_LOW:
            flags.append({"kind": "sets_vs_baseline", "direction": "up" if ratio > 1 else "down",
                          "text": (f"Working sets {ratio:.1f}x your {base_label} average "
                                   f"({_fmt(ws)} vs {_fmt(bws)} {per})")})
    vol, bvol = win["volume_kg"], base["volume_kg"]
    if bvol >= MIN_BASE_VOLUME_KG:
        ratio = vol / bvol
        if ratio >= RATIO_HIGH or ratio <= RATIO_LOW:
            flags.append({"kind": "volume_vs_baseline", "direction": "up" if ratio > 1 else "down",
                          "text": (f"Training volume {ratio:.1f}x your {base_label} average "
                                   f"({round(vol)} vs {round(bvol)} kg {per})")})
    for m in muscles:
        now, before = win_muscle.get(m, 0.0), base_muscle.get(m, 0.0)
        name = m.replace("_", " ").capitalize()
        if before >= MIN_BASE_MUSCLE_SETS and now / before >= MUSCLE_RATIO_HIGH:
            flags.append({"kind": "muscle_spike", "direction": "up",
                          "text": (f"{name} sets {now / before:.1f}x your {base_label} average "
                                   f"({_fmt(now)} vs {_fmt(before)} {per})")})
        if before >= MISSED_BASE_SETS and now == 0:
            flags.append({"kind": "muscle_missed", "direction": "down",
                          "text": f"No {name.lower()} sets {period} (about {_fmt(before)} {per} before)"})
    total = push + pull
    if total >= PUSH_PULL_MIN_SETS and (pull == 0 or push == 0 or push / pull > PUSH_PULL_HIGH
                                        or push / pull < PUSH_PULL_LOW):
        flags.append({"kind": "push_pull_imbalance", "direction": "imbalance",
                      "text": (f"Push vs pull is {_fmt(push)} to {_fmt(pull)} working sets {period}")})
    if win["sessions"] == 0 and raw_base["sessions"] > 0:
        flags.append({"kind": "no_sessions", "direction": "down",
                      "text": (f"No sessions {period} (you usually train about "
                               f"{_fmt(base['sessions'])} {per})")})

    return {
        "window": {"start": window_start.isoformat(), "end": today.isoformat(), "days": days},
        "sessions": int(win["sessions"]),
        "working_sets": int(ws),
        "volume_kg": round(vol, 1),
        "duration_min": round(win["duration_min"], 1),
        "baseline": {"start": base_start.isoformat(), "end": base_end.isoformat(),
                     "sessions": round(base["sessions"], 1), "working_sets": round(bws, 1),
                     "volume_kg": round(bvol, 1)},
        "by_muscle": by_muscle,
        "push_pull": push_pull,
        "flags": flags,
    }


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def consistency(workouts, tz, *, today: date, weeks: int = 12) -> dict:
    """Sessions per Monday-start week over the last ``weeks`` weeks (the current one included but excluded
    from averages), the current streak over all workouts, the longest gap between sessions in that range
    (rest days between two sessions, None with fewer than two), and the weekdays usually trained."""
    current = _week_start(today)
    first = current - timedelta(weeks=weeks - 1)
    rows = {first + timedelta(weeks=i): {"sessions": 0, "duration": 0.0, "sets": 0} for i in range(weeks)}
    weekdays: dict[date, set[int]] = defaultdict(set)
    all_days: list[date] = []
    in_range: list[date] = []
    for workout in workouts or []:
        day = _local_day(workout, tz)
        if day is None:
            continue
        all_days.append(day)
        week = _week_start(day)
        if week not in rows:
            continue
        row = rows[week]
        row["sessions"] += 1
        row["duration"] += _duration_min(workout)
        row["sets"] += len(_working_sets(workout))
        weekdays[week].add(day.weekday())
        in_range.append(day)

    week_rows = [{"week_start": w.isoformat(), "sessions": r["sessions"],
                  "duration_min": round(r["duration"], 1), "working_sets": r["sets"]}
                 for w, r in rows.items()]
    complete = [w for w in rows if w != current]
    avg = round(sum(rows[w]["sessions"] for w in complete) / len(complete), 1) if complete else None

    active = {_week_start(d) for d in all_days}
    cursor = current if current in active else current - timedelta(weeks=1)
    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(weeks=1)

    dates = sorted(set(in_range))
    longest = max((b - a).days - 1 for a, b in zip(dates, dates[1:])) if len(dates) >= 2 else None

    usual: list[str] = []
    if complete:
        for index, name in enumerate(WEEKDAYS):
            count = sum(1 for w in complete if index in weekdays.get(w, set()))
            if count >= USUAL_SHARE * len(complete):
                usual.append(name)

    last = max(all_days) if all_days else None
    return {
        "weeks": week_rows,
        "avg_sessions_per_week": avg,
        "current_streak_weeks": streak,
        "longest_gap_days": longest,
        "last_workout_date": last.isoformat() if last else None,
        "days_since_last_workout": (today - last).days if last else None,
        "usual_days": usual,
    }
