"""``pre_tool_call`` hook: every Hevy write goes through Hermes' human-approval gate.

The hook returns ``{"action": "approve", "message": ...}``, which asks the same gate as dangerous
shell commands (CLI prompt, Desktop/gateway dialog; cron follows ``approvals.cron_mode`` and any
other unattended context fails closed), so the model cannot skip the confirmation. The message is
built from the call's arguments alone: the hook runs inside the turn and must not touch the network.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .runtime import RUNTIME, Runtime

logger = logging.getLogger(__name__)

MAX_MESSAGE = 1200

ACTIONS = {
    "hevy_log_workout": "Log a new workout in Hevy",
    "hevy_update_workout": "Overwrite a workout in Hevy",
    "hevy_save_routine": "Save a routine in Hevy",
    "hevy_create_routine_folder": "Create a routine folder in Hevy",
    "hevy_create_exercise": "Create a custom exercise in Hevy",
    "hevy_log_body_measurement": "Save body measurements in Hevy",
}


def _text(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _set_text(s: Any) -> str:
    if not isinstance(s, dict):
        return "?"
    parts = []
    if s.get("weight_kg") is not None:
        parts.append(f"{s['weight_kg']} kg")
    elif s.get("weight_lbs") is not None:
        parts.append(f"{s['weight_lbs']} lb")
    rep_range = s.get("rep_range") if isinstance(s.get("rep_range"), dict) else None
    if rep_range:
        parts.append(f"x {rep_range.get('start')}-{rep_range.get('end')}")
    elif s.get("reps") is not None:
        parts.append(f"x {s['reps']}")
    if s.get("duration_seconds") is not None:
        parts.append(f"{s['duration_seconds']} s")
    if s.get("distance_meters") is not None:
        parts.append(f"{s['distance_meters']} m")
    if s.get("type") and s["type"] != "normal":
        parts.append(f"({s['type']})")
    return " ".join(parts) or "set"


def _exercise_lines(exercises: Any) -> list[str]:
    lines = []
    for item in exercises if isinstance(exercises, list) else []:
        if not isinstance(item, dict):
            continue
        sets = item.get("sets") if isinstance(item.get("sets"), list) else []
        shown = ", ".join(_set_text(s) for s in sets[:6]) + (f", +{len(sets) - 6} more" if len(sets) > 6 else "")
        lines.append(f"- {_text(item.get('exercise') or item.get('exercise_template_id') or '?', 50)}: {shown}")
    return lines


def describe_call(tool_name: str, args: dict) -> str:
    """A short, human-readable summary of what a write tool call will change."""
    lines = [ACTIONS.get(tool_name, f"Write to Hevy ({tool_name})")]
    if tool_name in ("hevy_log_workout", "hevy_update_workout"):
        if args.get("id"):
            lines.append(f"Workout id: {_text(args['id'], 60)} (fields not listed are kept)")
        if args.get("title"):
            lines.append(f"Title: {_text(args['title'])}")
        when = args.get("start_time")
        end = args.get("end_time") or (f"{args['duration_min']} min" if args.get("duration_min") else None)
        if when or end:
            lines.append(f"Time: {_text(when or 'unchanged', 40)} → {_text(end or 'unchanged', 40)}")
        if "is_private" in args:
            lines.append(f"Private: {'yes' if args.get('is_private') else 'no'}")
        if "exercises" in args:
            lines.append("Exercises (replace all):" if args.get("id") else "Exercises:")
            lines += _exercise_lines(args.get("exercises"))
    elif tool_name == "hevy_save_routine":
        lines[0] = "Update a routine in Hevy" if args.get("routine_id") else "Create a routine in Hevy"
        if args.get("routine_id"):
            lines.append(f"Routine id: {_text(args['routine_id'], 60)} (fields not listed are kept)")
        if args.get("title"):
            lines.append(f"Title: {_text(args['title'])}")
        if args.get("folder_id") is not None:
            lines.append(f"Folder id: {args['folder_id']}")
        if "exercises" in args:
            lines.append("Exercises (replace all):" if args.get("routine_id") else "Exercises:")
            lines += _exercise_lines(args.get("exercises"))
    elif tool_name == "hevy_create_routine_folder":
        lines.append(f"Title: {_text(args.get('title', ''))}")
    elif tool_name == "hevy_create_exercise":
        lines.append(f"Title: {_text(args.get('title', ''))} ({args.get('exercise_type', '?')}, "
                     f"{args.get('equipment_category', '?')}, {args.get('muscle_group', '?')})")
    elif tool_name == "hevy_log_body_measurement":
        lines.append(f"Date: {args.get('date') or 'today'}")
        lines += [f"- {key}: {value}" for key, value in args.items()
                  if key not in ("date", "raw", "clear") and value is not None]
        clear = args.get("clear")
        if isinstance(clear, list) and clear:
            lines.append(f"- remove: {', '.join(map(str, clear))}")
    message = "\n".join(lines)
    return message if len(message) <= MAX_MESSAGE else message[: MAX_MESSAGE - 1] + "…"


class WriteApproval:
    def __init__(self, write_tools: frozenset, runtime: Runtime = RUNTIME) -> None:
        self._write_tools = write_tools
        self._runtime = runtime

    def __call__(self, tool_name: str = "", args: Optional[dict] = None, **_kwargs: Any) -> Optional[dict]:
        if tool_name not in self._write_tools:
            return None
        try:
            if not self._runtime.flag("confirm_writes", True):
                return None
            message = describe_call(tool_name, dict(args or {}))
        except Exception:  # a broken preview must still ask, never skip the gate
            logger.debug("hevy approval preview failed", exc_info=True)
            message = ACTIONS.get(tool_name, f"Write to Hevy ({tool_name})")
        return {"action": "approve", "message": message}
