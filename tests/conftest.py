"""Shared fixtures: a fake Hevy API behind ``httpx.MockTransport``. No network."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TODAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)  # 18:00 in Madrid
TZ = ZoneInfo("Europe/Madrid")
API_KEY = "test-hevy-api-key-0123456789"
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class Clock:
    def __init__(self, now: float = NOW.timestamp()) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Seq(list):
    """Route answers consumed one per request; the last one repeats."""


class FakeHevy:
    """Routes by ``(method, path)``, or by path alone for GET. A route is a value (JSON body), an
    ``httpx.Response``, a callable ``(request) -> value | Response`` or a ``Seq`` of those."""

    def __init__(self) -> None:
        self.routes: dict[Any, Any] = {}
        self.requests: list[httpx.Request] = []

    def calls(self, path: str, method: str = "GET") -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path and r.method == method]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None and request.method == "GET":
            route = self.routes.get(request.url.path)
        if isinstance(route, Seq):
            route = route.pop(0) if len(route) > 1 else route[0]
        if route is None:
            return httpx.Response(404, json={"error": "Not found"})
        if callable(route):
            route = route(request)
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)


def paged(items_key: str, items: list, page_size_default: int = 10) -> Callable[[httpx.Request], Any]:
    """A route that serves ``items`` the way Hevy paginates (``page``, ``pageSize``, ``page_count``)."""
    def route(request: httpx.Request) -> Any:
        page = int(request.url.params.get("page", 1))
        size = int(request.url.params.get("pageSize", page_size_default))
        count = max(1, -(-len(items) // size)) if items else 0
        if items and page > count:
            return httpx.Response(404, json={"error": "Page not found"})
        chunk = items[(page - 1) * size: page * size]
        return {"page": page, "page_count": count, items_key: chunk}
    return route


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fake() -> FakeHevy:
    return FakeHevy()


@pytest.fixture
def sleeps() -> list:
    return []


@pytest.fixture
def runtime(monkeypatch, fake, clock, sleeps, tmp_path):
    """The process-wide RUNTIME wired to the fake Hevy API: no key lookup, fixed day/zone, a cache in tmp_path."""
    from hermes_hevy.client import HevyClient
    from hermes_hevy.runtime import RUNTIME
    from hermes_hevy.store import WorkoutStore
    client = HevyClient(lambda: API_KEY, transport=httpx.MockTransport(fake.handler), clock=clock, sleep=sleeps.append)
    monkeypatch.setattr(RUNTIME, "_client", client)
    monkeypatch.setattr(RUNTIME, "_key_provider", lambda: API_KEY)
    monkeypatch.setattr(RUNTIME, "today", lambda: TODAY)
    monkeypatch.setattr(RUNTIME, "tz", lambda: TZ)
    monkeypatch.setattr(RUNTIME, "_get_config", lambda key, default=None: default)
    monkeypatch.setattr(RUNTIME, "store", WorkoutStore(lambda: tmp_path, clock=clock))
    return RUNTIME


@pytest.fixture
def hevy_data(fake):
    """Routes on the fake API served from the synthetic fixtures (read-only data)."""
    workouts = fixture("workouts")
    templates = fixture("exercise_templates")
    routines = fixture("routines")
    folders = fixture("routine_folders")
    measurements = fixture("body_measurements")
    fake.routes.update({
        "/v1/workouts": paged("workouts", workouts),
        "/v1/workouts/events": paged("events", []),
        "/v1/exercise_templates": paged("exercise_templates", templates, 100),
        "/v1/routines": paged("routines", routines),
        "/v1/routine_folders": paged("routine_folders", folders),
        "/v1/body_measurements": paged("body_measurements", measurements),
        "/v1/user/info": fixture("user_info"),
        "/v1/workouts/count": {"workout_count": len(workouts)},
    })
    for routine in routines:
        fake.routes[f"/v1/routines/{routine['id']}"] = {"routine": routine}
    return fake
