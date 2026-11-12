"""The ``/hevy`` slash command: ``status``, ``sync`` and ``help``."""

from __future__ import annotations

import logging
from datetime import datetime

from . import __version__
from .client import KEY_CLI, KEY_ENV, KEY_PAGE, KEY_SETTINGS, HevyError
from .runtime import RUNTIME

logger = logging.getLogger(__name__)

HELP = f"""/hevy status  key, account, time zone and workout cache
/hevy sync    fetch new, changed and deleted workouts from Hevy now
/hevy help    this text

Set up (needs Hevy Pro):
1. Create an API key at {KEY_PAGE}
2. Paste it in {KEY_SETTINGS}
   and select Save settings. Using a terminal instead: `{KEY_CLI}`.
   Never paste the key into a chat.
Then ask Hermes things like
"how is my bench press progressing?" or "build me a 3-day upper/lower routine"."""


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, RUNTIME.tz()).strftime("%Y-%m-%d %H:%M")


def status() -> str:
    lines = [f"Hevy plugin {__version__} (unofficial)"]
    if not RUNTIME.api_key():
        lines += [f"API key: not set. Create one at {KEY_PAGE} (Hevy Pro)",
                  f"and paste it in {KEY_SETTINGS} (from a terminal: `{KEY_CLI}`)."]
        return "\n".join(lines)
    lines.append(f"API key: set ({KEY_ENV})")
    client = RUNTIME.client()
    try:
        info = (client.get("/v1/user/info", ttl=0) or {}).get("data") or {}
        pro = {True: "Hevy Pro", False: "no Hevy Pro"}.get(info.get("is_pro"), "plan unknown")
        lines.append(f"Account: {info.get('name') or info.get('username') or '?'} ({pro}; "
                     f"units {info.get('weight_unit', 'kg')}, {info.get('distance_unit', 'kilometers')})")
        count = (client.get("/v1/workouts/count") or {}).get("workout_count")
        lines.append(f"Workouts in Hevy: {count if count is not None else '?'}")
    except HevyError as exc:
        lines.append(f"Account check: failed. {exc}")
    synced = RUNTIME.store.last_sync(client)
    lines.append(f"Workout cache: last synced {_when(synced)}" if synced else
                 "Workout cache: empty (filled on the first question about workouts, or with /hevy sync)")
    lines.append(f"Time zone: {RUNTIME.zone_choice().describe()}")
    confirm = RUNTIME.flag("confirm_writes", True)
    lines.append(f"Writes to Hevy: {'ask for approval each time' if confirm else 'no approval (confirm_writes off)'}")
    return "\n".join(lines)


def sync() -> str:
    client = RUNTIME.client()
    try:
        result = RUNTIME.store.sync(client, force=True)
    except HevyError as exc:
        return str(exc)
    kind = "Full sync" if result.full else "Sync"
    return (f"{kind} done: {result.updated} new or changed, {result.deleted} deleted, "
            f"{result.total} workouts cached.")


def handle(raw_args: str = "") -> str:
    words = (raw_args or "").strip().lower().split(maxsplit=1)
    sub = words[0] if words else "status"
    try:
        if sub == "status":
            return status()
        if sub == "sync":
            return sync()
    except Exception as exc:
        logger.exception("/hevy %s failed", sub)
        return f"/hevy {sub} failed ({type(exc).__name__})."
    return HELP
