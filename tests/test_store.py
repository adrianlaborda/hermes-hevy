from datetime import datetime, timezone

import httpx
import pytest

from conftest import API_KEY, NOW, paged
from hermes_hevy.client import HevyClient, HevyError
from hermes_hevy.store import WorkoutStore

WORKOUTS = "/v1/workouts"
EVENTS = "/v1/workouts/events"
TEMPLATES = "/v1/exercise_templates"
OTHER_KEY = "other-hevy-api-key-9876543210"


def workout(workout_id: str, start: str, title: str = "Leg day") -> dict:
    return {"id": workout_id, "title": title, "routine_id": None, "description": "", "start_time": start,
            "end_time": start, "updated_at": start, "created_at": start, "exercises": []}


def template(template_id: str, title: str) -> dict:
    return {"id": template_id, "title": title, "type": "weight_reps", "primary_muscle_group": "chest",
            "secondary_muscle_groups": [], "equipment": "barbell", "is_custom": False}


@pytest.fixture
def make_client(fake, clock, sleeps):
    clients = []

    def factory(key=lambda: API_KEY):
        client = HevyClient(key, transport=httpx.MockTransport(fake.handler), clock=clock, sleep=sleeps.append)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def store(tmp_path, clock):
    return WorkoutStore(lambda: tmp_path, clock=clock)


def ids(workouts: list[dict]) -> list[str]:
    return [w["id"] for w in workouts]


# -- sync ----------------------------------------------------------------------------------------

def test_full_sync_stores_all_pages(fake, client, store):
    items = [workout(f"w{i}", f"2026-10-{i + 1:02d}T18:00:00Z") for i in range(25)]
    fake.routes[WORKOUTS] = paged("workouts", items)

    result = store.sync(client)

    assert result.full is True
    assert (result.updated, result.deleted, result.total) == (25, 0, 25)
    assert result.synced_at == NOW.timestamp()
    assert len(fake.calls(WORKOUTS)) == 3
    assert len(store.workouts(client, sync=False)) == 25


def test_second_sync_within_interval_is_skipped(fake, client, store, clock):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    store.sync(client)
    fake.requests.clear()
    clock.now += 30

    result = store.sync(client)

    assert (result.full, result.updated, result.deleted) == (False, 0, 0)
    assert fake.requests == []


def test_incremental_sync_after_interval_sends_since_minus_five_minutes(fake, client, store, clock):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    fake.routes[EVENTS] = paged("events", [])
    store.sync(client)
    clock.now += 60

    result = store.sync(client)

    assert result.full is False
    events = fake.calls(EVENTS)
    assert len(events) == 1
    assert events[0].url.params["since"] == "2026-11-09T16:55:00Z"  # previous start 17:00Z minus 5 minutes


def test_updated_event_upserts_and_deleted_event_removes(fake, client, store, clock):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z"),
                                               workout("w2", "2026-11-07T18:00:00Z")])
    store.sync(client)
    fake.routes[EVENTS] = paged("events", [
        {"type": "updated", "workout": workout("w1", "2026-11-08T18:00:00Z", title="Changed")},
        {"type": "deleted", "id": "w2"},
        {"type": "updated", "workout": workout("w3", "2026-11-09T06:00:00Z")},
    ])
    clock.now += 60

    result = store.sync(client)

    assert (result.full, result.updated, result.deleted, result.total) == (False, 2, 1, 2)
    stored = {w["id"]: w for w in store.workouts(client, sync=False)}
    assert set(stored) == {"w1", "w3"}
    assert stored["w1"]["title"] == "Changed"


def test_force_bypasses_the_interval(fake, client, store, clock):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    fake.routes[EVENTS] = paged("events", [])
    store.sync(client)
    clock.now += 1

    result = store.sync(client, force=True)

    assert result.full is False
    assert len(fake.calls(EVENTS)) == 1


def test_failed_sync_propagates_and_keeps_previous_state(fake, client, store, clock):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    store.sync(client)
    fake.routes[EVENTS] = httpx.Response(401, json={"message": "bad key"})
    clock.now += 60

    with pytest.raises(HevyError):
        store.sync(client)
    assert store.last_sync(client) == NOW.timestamp()
    assert ids(store.workouts(client, sync=False)) == ["w1"]


# -- reads ---------------------------------------------------------------------------------------

def test_workouts_filter_by_instant_and_sort_newest_first(fake, client, store):
    fake.routes[WORKOUTS] = paged("workouts", [
        workout("a", "2026-11-01T10:00:00Z"),
        workout("b", "2026-11-05T09:30:00+02:00"),  # 07:30Z, sorts before "c" although its text sorts after
        workout("c", "2026-11-05T08:00:00.500Z"),
        workout("d", "2026-11-09T06:00:00Z"),
    ])
    store.sync(client)

    assert ids(store.workouts(client, sync=False)) == ["d", "c", "b", "a"]
    since = datetime(2026, 11, 5, 7, 30, tzinfo=timezone.utc)
    until = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)
    assert ids(store.workouts(client, since=since, until=until)) == ["c", "b"]


def test_each_key_gets_its_own_file_and_rows(fake, make_client, tmp_path, clock):
    store = WorkoutStore(lambda: tmp_path, clock=clock)
    items = {API_KEY: [workout("mine", "2026-11-08T18:00:00Z")], OTHER_KEY: [workout("theirs", "2026-11-08T19:00:00Z")]}
    fake.routes[WORKOUTS] = lambda request: paged("workouts", items[request.headers["api-key"]])(request)
    mine, theirs = make_client(lambda: API_KEY), make_client(lambda: OTHER_KEY)

    store.sync(mine)
    store.sync(theirs)

    assert ids(store.workouts(mine, sync=False)) == ["mine"]
    assert ids(store.workouts(theirs, sync=False)) == ["theirs"]
    assert len(list(tmp_path.glob("cache-*.sqlite3"))) == 2


def test_workout_is_served_from_cache_or_fetched_and_stored(fake, client, store):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    store.sync(client)
    fake.requests.clear()

    assert store.workout(client, "w1")["id"] == "w1"
    assert fake.requests == []

    fake.routes["/v1/workouts/w2"] = workout("w2", "2026-11-07T18:00:00Z", title="Fetched")
    assert store.workout(client, "w2")["title"] == "Fetched"
    assert len(fake.calls("/v1/workouts/w2")) == 1
    assert ids(store.workouts(client, sync=False)) == ["w1", "w2"]

    fake.requests.clear()
    assert store.workout(client, "w2")["title"] == "Fetched"
    assert fake.requests == []


def test_workout_not_found_returns_none(fake, client, store):
    fake.routes["/v1/workouts/missing"] = httpx.Response(404, json={"error": "Not found"})

    assert store.workout(client, "missing") is None
    assert store.workouts(client, sync=False) == []


def test_put_workout_replaces_the_stored_copy(fake, client, store):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    store.sync(client)

    store.put_workout(client, workout("w1", "2026-11-08T18:00:00Z", title="Edited"))
    store.put_workout(client, workout("w2", "2026-11-09T06:00:00Z"))

    stored = store.workouts(client, sync=False)
    assert ids(stored) == ["w2", "w1"]
    assert stored[1]["title"] == "Edited"


# -- templates -----------------------------------------------------------------------------------

def test_templates_fetched_once_then_served_until_older_than_max_age(fake, client, store, clock):
    fake.routes[TEMPLATES] = paged("exercise_templates", [template("t1", "Bench"), template("t2", "Squat")], 100)

    first = store.templates(client)
    assert set(first) == {"t1", "t2"}
    assert first["t1"]["title"] == "Bench"
    assert fake.calls(TEMPLATES)[0].url.params["pageSize"] == "100"

    clock.now += 3600
    assert store.templates(client) == first
    assert len(fake.calls(TEMPLATES)) == 1

    clock.now += 86401
    store.templates(client)
    assert len(fake.calls(TEMPLATES)) == 2


def test_put_template_adds_to_stored_templates(fake, client, store):
    fake.routes[TEMPLATES] = paged("exercise_templates", [template("t1", "Bench")], 100)
    store.templates(client)

    store.put_template(client, template("t9", "Custom row"))

    assert set(store.templates(client)) == {"t1", "t9"}
    assert len(fake.calls(TEMPLATES)) == 1


# -- maintenance ---------------------------------------------------------------------------------

def test_clear_removes_rows_and_sync_state_so_next_sync_is_full(fake, client, store, clock):
    fake.routes[WORKOUTS] = paged("workouts", [workout("w1", "2026-11-08T18:00:00Z")])
    store.sync(client)
    store.clear(client)

    assert store.workouts(client, sync=False) == []
    assert store.last_sync(client) is None

    fake.requests.clear()
    clock.now += 60
    result = store.sync(client)
    assert result.full is True
    assert len(fake.calls(WORKOUTS)) == 1
    assert len(fake.calls(EVENTS)) == 0
