"""Plugin settings, the API key route, the shared client and the workout cache.

The key is read through Hermes' secret-scope API on every request, which honours the active
profile; the plugin never parses ``.env`` itself and never caches the key.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from .client import KEY_ENV, HevyClient
from .dates import known_zone, zone
from .store import WorkoutStore

PLUGIN_ID = "hevy"
AUTO_ZONE = "auto"

ZONE_SOURCES = {
    "setting": "set in the plugin settings",
    "hermes": "from Hermes' timezone setting",
    "server": "the server's local time",
}


class ZoneChoice(NamedTuple):
    tz: tzinfo
    name: str
    source: str  # a ZONE_SOURCES key

    def describe(self) -> str:
        return f"{self.name} ({ZONE_SOURCES[self.source]})"


ConfigGetter = Callable[[str, Any], Any]


def _clean(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_api_key() -> Optional[str]:
    """The key from the active profile's secret scope.

    Agent turns, cron fires and the dashboard bind that scope. A slash command dispatched from
    Desktop or the TUI (``command.dispatch``) binds only the session's Hermes home, so only when NO
    scope is bound is the profile's scope rebuilt, through Hermes' own
    ``build_profile_secret_scope``, for the home Hermes bound for the call. A bound scope stays
    authoritative."""
    try:
        from agent import secret_scope
    except Exception:  # outside Hermes
        return None
    try:
        value = _clean(secret_scope.get_secret(KEY_ENV))
    except Exception:  # UnscopedSecretError: no profile scope while multiplexing
        value = None
    if value is not None:
        return value
    try:
        if secret_scope.current_secret_scope() is not None:
            return None
        from hermes_constants import get_hermes_home
        return _clean(secret_scope.build_profile_secret_scope(get_hermes_home()).get(KEY_ENV))
    except Exception:
        return None


def key_configured() -> bool:
    """Passive check for ``check_fn``: no network, no installs."""
    return read_api_key() is not None


def data_dir() -> Path:
    """``<hermes home>/plugin-data/hevy/``, resolved on every call so it follows the active profile."""
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN_ID)
    except Exception:  # outside Hermes, or a Hermes without the helper
        try:
            from hermes_constants import get_hermes_home
            home = Path(get_hermes_home())
        except Exception:
            home = Path.home() / ".hermes"
        path = home / "plugin-data" / PLUGIN_ID
        path.mkdir(parents=True, exist_ok=True)
        return path


def _hermes_timezone() -> str:
    """The active profile's ``timezone`` from its ``config.yaml``, through Hermes' public config API."""
    try:
        from hermes_cli.config import load_config_readonly
        return str((load_config_readonly() or {}).get("timezone") or "").strip()
    except Exception:
        return ""


def hermes_plugin_setting(key: str, default: Any = None) -> Any:
    """``plugins.entries.hevy.settings.<key>`` from the active profile's config, for code that runs
    without a plugin context (the Desktop panel's backend routes)."""
    try:
        from hermes_cli.config import load_config_readonly
        entries = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    except Exception:
        return default
    entry = entries.get(PLUGIN_ID) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return default
    for section in ("settings", "config"):
        values = entry.get(section)
        if isinstance(values, dict) and key in values:
            return values[key]
    return default


class Runtime:
    def __init__(self, get_config: Optional[ConfigGetter] = None,
                 key_provider: Callable[[], Optional[str]] = read_api_key,
                 store: Optional[WorkoutStore] = None) -> None:
        self._get_config = get_config or (lambda _key, default=None: default)
        self._key_provider = key_provider
        self._client: Optional[HevyClient] = None
        self._lock = threading.Lock()
        self.store = store or WorkoutStore(data_dir)

    def bind(self, get_config: ConfigGetter) -> None:
        self._get_config = get_config

    def setting(self, key: str, default: Any = None) -> Any:
        try:
            value = self._get_config(key, default)
        except Exception:
            return default
        return default if value is None else value

    def flag(self, key: str, default: bool = False) -> bool:
        value = self.setting(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def api_key(self) -> Optional[str]:
        return self._key_provider()

    def has_key(self) -> bool:
        return bool(self.api_key())

    def client(self) -> HevyClient:
        with self._lock:
            if self._client is None:
                self._client = HevyClient(self._key_provider)
            return self._client

    def zone_choice(self) -> ZoneChoice:
        """The plugin setting unless it is ``auto`` or empty; then Hermes' ``timezone``; then the
        server's local zone. Hevy's API exposes no profile time zone."""
        setting = str(self.setting("timezone", "") or "").strip()
        if setting and setting.lower() != AUTO_ZONE and known_zone(setting):
            return ZoneChoice(zone(setting), setting, "setting")
        hermes = _hermes_timezone()
        if hermes and known_zone(hermes):
            return ZoneChoice(zone(hermes), hermes, "hermes")
        local = zone(None)
        return ZoneChoice(local, getattr(local, "key", None) or datetime.now(local).tzname() or "UTC", "server")

    def tz(self) -> tzinfo:
        return self.zone_choice().tz

    def today(self) -> date:
        return datetime.now(self.tz()).date()

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None


RUNTIME = Runtime()
