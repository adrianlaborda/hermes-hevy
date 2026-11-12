"""Registration against a recording context, agreement with plugin.yaml, the key route and /hevy."""

import importlib.util
import inspect
import os
import sys
import types
from pathlib import Path

import httpx
import pytest
import yaml

from conftest import API_KEY, ROOT, fixture
from hermes_hevy import commands

MANIFEST = yaml.safe_load((ROOT / "plugin.yaml").read_text())


class RecordingContext:
    def __init__(self):
        self.tools, self.hooks, self.commands, self.skills, self.blueprints = {}, {}, {}, {}, {}
        self.unload = []

    def get_config(self, key, default=None):
        return default

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, emoji="", **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "check_fn": check_fn,
                            "requires_env": requires_env}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands[name] = handler

    def register_skill(self, name, path, description="", **kw):
        self.skills[name] = Path(path)

    def register_automation_blueprint(self, key, **fields):
        self.blueprints[key] = fields

    def on_unload(self, callback):
        self.unload.append(callback)


def _load_and_register():
    spec = importlib.util.spec_from_file_location("hermes_plugins_hevy", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # the same entry point Hermes' loader imports
    recording = RecordingContext()
    module.register(recording)
    return recording


@pytest.fixture
def ctx():
    return _load_and_register()


def test_registrations_match_the_manifest(ctx):
    assert list(ctx.tools) == MANIFEST["provides_tools"]
    assert sorted(ctx.hooks) == sorted(MANIFEST["provides_hooks"])
    required = [e["name"] for e in MANIFEST["requires_env"]]
    assert all(t["requires_env"] == required for t in ctx.tools.values())
    assert set(MANIFEST["config_schema"]) == {"api_key", "timezone", "confirm_writes"}
    # The Desktop settings form stores the key under the same env name the plugin reads.
    api_key = MANIFEST["config_schema"]["api_key"]
    assert api_key["type"] == "secret" and api_key["env"] == "HEVY_API_KEY" == required[0]
    assert list(MANIFEST["config_schema"])[0] == "api_key"
    assert MANIFEST["config_schema"]["confirm_writes"]["default"] is True


def test_versions_agree():
    from hermes_hevy import __version__
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert MANIFEST["version"] == __version__ and f'version = "{__version__}"' in pyproject


def test_every_write_tool_is_gated(ctx):
    from hermes_hevy.tools import WRITE_TOOLS
    hook = ctx.hooks["pre_tool_call"]
    assert "kwargs" in str(inspect.signature(hook))
    assert WRITE_TOOLS <= set(ctx.tools)
    for name in ctx.tools:
        result = hook(tool_name=name, args={}, task_id="t", session_id="s")
        assert (result is not None and result["action"] == "approve") == (name in WRITE_TOOLS)
        if name in WRITE_TOOLS:
            assert "approve it before it runs" in ctx.tools[name]["schema"]["description"]


class FakeSecretScope:
    """``agent.secret_scope`` and ``hermes_constants`` as Hermes exposes them to plugins: a bound
    scope (or none), ``get_secret`` with its fail-closed raise, and per-home ``.env`` scopes."""

    def __init__(self, monkeypatch) -> None:
        self.bound = None          # the installed scope mapping, None when no scope is bound
        self.environ = {}          # the process env as it was at launch
        self.multiplex = True      # a multi-profile host: unscoped reads fail closed
        self.homes = {}            # home -> what build_profile_secret_scope(home) returns
        self.home = "/home/hermes/.hermes"
        self.built = []
        module = types.ModuleType("agent.secret_scope")
        module.get_secret = self.get_secret
        module.current_secret_scope = lambda: self.bound
        module.build_profile_secret_scope = self.build
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: Path(self.home)
        agent = types.ModuleType("agent")
        agent.secret_scope = module
        monkeypatch.setitem(sys.modules, "agent", agent)
        monkeypatch.setitem(sys.modules, "agent.secret_scope", module)
        monkeypatch.setitem(sys.modules, "hermes_constants", constants)

    def get_secret(self, name, default=None):
        if self.bound is not None:
            return self.bound.get(name, default if self.multiplex else self.environ.get(name, default))
        if self.multiplex:
            raise RuntimeError("UnscopedSecretError: no profile secret scope while multiplexing")
        return self.environ.get(name, default)

    def build(self, home):
        self.built.append(str(home))
        if str(home) == "broken":
            raise OSError("unreadable")
        return dict(self.homes.get(str(home), {}))


def test_key_is_read_through_get_secret(ctx, monkeypatch):
    fake = FakeSecretScope(monkeypatch)
    fake.bound = {}
    check = next(iter(ctx.tools.values()))["check_fn"]
    assert check() is False
    fake.bound["HEVY_API_KEY"] = "  "
    assert check() is False
    fake.bound["HEVY_API_KEY"] = API_KEY
    assert check() is True
    assert fake.built == []  # a scoped read never rebuilds anything


def test_a_bound_scope_is_authoritative(ctx, monkeypatch):
    fake = FakeSecretScope(monkeypatch)
    fake.bound = {}
    fake.homes[fake.home] = {"HEVY_API_KEY": API_KEY}
    check = next(iter(ctx.tools.values()))["check_fn"]
    assert check() is False and fake.built == []


def test_without_a_scope_the_bound_homes_key_is_used(monkeypatch):
    from hermes_hevy import runtime

    fake = FakeSecretScope(monkeypatch)
    fake.homes[fake.home] = {"HEVY_API_KEY": f" {API_KEY} "}
    assert runtime.read_api_key() == API_KEY and fake.built == [fake.home]
    fake.home = "/home/hermes/.hermes/profiles/other"  # a secondary profile's session
    assert runtime.read_api_key() is None  # never the launch profile's key
    fake.home = "broken"
    assert runtime.read_api_key() is None


def test_outside_hermes_there_is_no_key(monkeypatch):
    from hermes_hevy import runtime

    monkeypatch.setitem(sys.modules, "agent", None)
    assert runtime.read_api_key() is None


def test_key_check_is_not_cached_by_hermes(monkeypatch):
    marked = []
    registry = types.ModuleType("tools.registry")
    registry.no_cache_check_fn = lambda fn: marked.append(fn) or fn
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", registry)
    recording = _load_and_register()
    from hermes_hevy.runtime import key_configured
    assert marked and all(t["check_fn"] in marked for t in recording.tools.values())
    assert marked[0].__name__ == key_configured.__name__


def test_data_lives_in_the_profiles_plugin_data(monkeypatch, tmp_path):
    from hermes_hevy import runtime

    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setitem(sys.modules, "plugins", None)  # a Hermes without plugin_storage
    assert runtime.data_dir() == tmp_path / "plugin-data" / "hevy" and runtime.data_dir().is_dir()


def test_command_skill_blueprint(ctx):
    assert "hevy" in ctx.commands
    assert ctx.skills["hevy"].read_text().startswith("---\nname: hevy\n")
    blueprint = ctx.blueprints["weekly-training-review"]
    assert "hevy_training_load" in blueprint["prompt_template"] and "{goal}" in blueprint["prompt_template"]
    assert blueprint["skills"] == ("hevy:hevy",)


def _hermes_source():
    for candidate in (os.environ.get("HERMES_AGENT_SRC"), Path.home() / ".hermes" / "hermes-agent"):
        if candidate and (Path(candidate) / "cron" / "blueprint_plugins.py").exists():
            return str(candidate)
    return None


@pytest.mark.skipif(_hermes_source() is None, reason="needs a Hermes Agent checkout (HERMES_AGENT_SRC)")
def test_blueprint_is_accepted_by_hermes(ctx, monkeypatch):
    monkeypatch.syspath_prepend(_hermes_source())
    pytest.importorskip("ruamel.yaml", reason="run with Hermes' own interpreter to include this check")
    from cron.blueprint_catalog import fill_blueprint
    from cron.blueprint_plugins import build_plugin_blueprint

    blueprint = build_plugin_blueprint("hevy", "weekly-training-review", **ctx.blueprints["weekly-training-review"])
    filled = fill_blueprint(blueprint, {"time": "19:30", "day": "sunday"})
    assert filled["schedule"] == "30 19 * * 0"
    assert "general strength" in filled["prompt"]


def test_status_command(fake, runtime, hevy_data):
    out = commands.handle("status")
    assert "API key: set" in out and API_KEY not in out
    assert "Account: Test Lifter (Hevy Pro; units kg, kilometers)" in out
    assert f"Workouts in Hevy: {len(fixture('workouts'))}" in out
    assert "Workout cache: empty" in out and "ask for approval each time" in out
    fake.routes["/v1/user/info"] = httpx.Response(401, json={"error": "Unauthorized"})
    runtime.client().invalidate()
    assert "Account check: failed. " in commands.handle("status")


def test_sync_command(runtime, hevy_data):
    assert commands.handle("sync") == f"Full sync done: {len(fixture('workouts'))} new or changed, 0 deleted, " \
                                      f"{len(fixture('workouts'))} workouts cached."
    assert "Workout cache: last synced" in commands.handle("status")


def test_status_without_key(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_key_provider", lambda: None)
    out = commands.handle("")
    assert "API key: not set" in out
    assert out.index("Settings > Plugins > hevy") < out.index("hermes config set")


def test_help(runtime):
    out = commands.handle("help")
    assert "/hevy sync" in out
    assert out.index("Settings > Plugins > hevy") < out.index("hermes config set")
    assert commands.handle("nonsense") == commands.HELP


def test_package_imports_are_relative():
    """Hermes loads the plugin under its own module name, so ``import hermes_hevy`` only works in tests."""
    for path in (ROOT / "hermes_hevy").glob("*.py"):
        text = path.read_text()
        assert "from hermes_hevy" not in text and "import hermes_hevy" not in text, path.name
