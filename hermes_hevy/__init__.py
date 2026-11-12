"""Hevy for Hermes Agent, through Hevy's official public API (unofficial plugin)."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"

TOOLSET = "hevy"
SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "hevy"

WEEKLY_REVIEW_PROMPT = (
    "Write the user's weekly strength training review from their Hevy data, following the hevy skill. "
    "Call hevy_training_load with days=7, hevy_consistency with weeks=8 and hevy_records with days=7. "
    "If garmin_trends is available, also call it with days=28 and relate recovery (HRV, resting heart rate, "
    "sleep) to the training. In at most 10 short lines: sessions and working sets against their usual week, "
    "muscle groups that got much more or less work, new personal records, consistency, and one practical "
    "suggestion for next week ({goal}). Use the user's language. If the Hevy tools are unavailable or the "
    "key was rejected, reply with one line saying so and how to fix it."
)


def _uncached_check(fn) -> None:
    """Hermes caches a ``check_fn`` verdict for 30 s; the key check is a local secret lookup (no
    network), the kind Hermes' ``no_cache_check_fn`` exists for, so a key saved a moment ago counts."""
    try:
        from tools.registry import no_cache_check_fn
        no_cache_check_fn(fn)
    except Exception:  # outside Hermes, or a Hermes without the marker: keep the cached verdict
        pass


def register(ctx) -> None:
    # No network or attribute reads beyond get_config here: register() also runs in validators.
    from .approval import WriteApproval
    from .commands import handle
    from .runtime import RUNTIME, key_configured
    from .tools import TOOLS, WRITE_TOOLS

    RUNTIME.bind(ctx.get_config)
    _uncached_check(key_configured)
    for name, schema, handler, emoji in TOOLS:
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=key_configured, requires_env=["HEVY_API_KEY"], emoji=emoji)
    ctx.register_hook("pre_tool_call", WriteApproval(WRITE_TOOLS, RUNTIME))
    ctx.register_command("hevy", handler=handle, args_hint="[status|sync|help]",
                         description="Hevy: status, sync workouts now, help")
    ctx.register_skill("hevy", SKILL_DIR / "SKILL.md",
                       description="Use and interpret Hevy strength training data and write workouts, routines "
                                   "and measurements to Hevy")
    ctx.register_automation_blueprint(
        "weekly-training-review",
        title="Weekly training review",
        description="Every week: sessions, sets per muscle group against your usual week, new personal records "
                    "and consistency from Hevy, with recovery from Garmin when that plugin is on.",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=WEEKLY_REVIEW_PROMPT,
        category="weekly",
        slots=[
            {"name": "time", "type": "time", "label": "What time?", "default": "19:00",
             "help": "24h local time"},
            {"name": "day", "type": "enum", "label": "Which day?", "default": "sunday",
             "options": ("sunday", "monday", "friday", "saturday")},
            {"name": "goal", "type": "text", "label": "Training goal",
             "default": "general strength", "help": "for example: hypertrophy, powerlifting meet prep, fat loss"},
            {"name": "deliver", "type": "enum", "label": "Where to deliver?", "default": "origin",
             "options": ("origin", "local", "telegram", "discord", "email"), "strict": False},
        ],
        skills=("hevy:hevy",),
        tags=("weekly", "fitness", "hevy"),
    )
    ctx.on_unload(RUNTIME.close)
