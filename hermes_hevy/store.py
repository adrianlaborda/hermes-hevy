"""On-disk cache of workouts and exercise templates, one SQLite file per API key.

Hevy's ``/v1/workouts`` has no date filter and returns at most 10 workouts a page, so listing a month
of training would take many requests. The cache mirrors the workouts once (full sync) and then keeps
them fresh with ``/v1/workouts/events?since=``, which reports updates and deletions. Templates are
mirrored the same way, refreshed only when older than ``max_age``. Each key gets its own file (named
by the key fingerprint), so two profiles never share rows.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from .client import HevyClient, NotFoundError
from .dates import iso_utc, parse_instant

PAGE_SIZE = 10
TEMPLATE_PAGE_SIZE = 100
# Events are read from a little before the last sync: the server may stamp an event slightly earlier
# than the moment we synced, and re-applying an event is harmless.
SINCE_OVERLAP = timedelta(minutes=5)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (id TEXT PRIMARY KEY, start_time TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass(frozen=True)
class SyncResult:
    full: bool
    updated: int
    deleted: int
    total: int
    synced_at: float  # clock() when the sync started; a skipped sync reports the last one


class WorkoutStore:
    def __init__(self, data_dir: Callable[[], Path], *, clock: Callable[[], float] = time.time,
                 min_sync_interval: float = 60.0) -> None:
        self._data_dir = data_dir
        self._clock = clock
        self._min_sync_interval = min_sync_interval
        self._lock = threading.Lock()

    # -- workouts ----------------------------------------------------------------------------

    def sync(self, client: HevyClient, *, force: bool = False) -> SyncResult:
        """Bring the cache up to date. Full on first use; otherwise the events since the last sync.

        Skipped when the last sync is younger than ``min_sync_interval``, unless ``force``."""
        with self._lock, self._db(client) as db:
            start = self._clock()
            anchor = parse_instant(_meta(db, "synced_since"))
            if anchor is None:
                items = list(client.paginate("/v1/workouts", "workouts", page_size=PAGE_SIZE, ttl=0))
                db.execute("DELETE FROM workouts")
                for workout in items:
                    _upsert(db, workout)
                _set_meta(db, start, iso_utc(datetime.fromtimestamp(start, timezone.utc)))
                return SyncResult(True, len(items), 0, _count(db), start)

            synced_at = float(_meta(db, "synced_at") or 0.0)
            if not force and start - synced_at < self._min_sync_interval:
                return SyncResult(False, 0, 0, _count(db), synced_at)

            since = iso_utc(anchor - SINCE_OVERLAP)
            events = client.paginate("/v1/workouts/events", "events", page_size=PAGE_SIZE,
                                     params={"since": since}, ttl=0)
            updated = deleted = 0
            for event in list(events):
                if event.get("type") == "deleted":
                    db.execute("DELETE FROM workouts WHERE id = ?", (str(event.get("id")),))
                    deleted += 1
                elif event.get("type") == "updated" and isinstance(event.get("workout"), dict):
                    _upsert(db, event["workout"])
                    updated += 1
            _set_meta(db, start, iso_utc(datetime.fromtimestamp(start, timezone.utc)))
            return SyncResult(False, updated, deleted, _count(db), start)

    def workouts(self, client: HevyClient, *, since: Optional[datetime] = None,
                 until: Optional[datetime] = None, sync: bool = True) -> list[dict]:
        """Workouts with ``since <= start_time < until`` (either bound optional), newest first."""
        if sync:
            self.sync(client)
        with self._lock, self._db(client) as db:
            rows = db.execute("SELECT body, start_time FROM workouts").fetchall()
        found = []
        for body, start_time in rows:
            moment = parse_instant(start_time)
            if since or until:
                if moment is None or (since and moment < since) or (until and moment >= until):
                    continue
            found.append((moment or EPOCH, json.loads(body)))
        found.sort(key=lambda pair: pair[0], reverse=True)
        return [workout for _, workout in found]

    def workout(self, client: HevyClient, workout_id: str) -> Optional[dict]:
        """A workout from the cache, else fetched from Hevy and stored. None when Hevy has no such workout."""
        with self._lock, self._db(client) as db:
            row = db.execute("SELECT body FROM workouts WHERE id = ?", (str(workout_id),)).fetchone()
        if row:
            return json.loads(row[0])
        try:
            workout = client.get(f"/v1/workouts/{workout_id}", ttl=0)
        except NotFoundError:
            return None
        if isinstance(workout, dict):
            self.put_workout(client, workout)
        return workout

    def put_workout(self, client: HevyClient, workout: dict) -> None:
        """Store a workout just created or updated, so the next read sees it without a sync."""
        with self._lock, self._db(client) as db:
            _upsert(db, workout)

    def last_sync(self, client: HevyClient) -> Optional[float]:
        with self._lock, self._db(client) as db:
            value = _meta(db, "synced_at")
        return float(value) if value else None

    # -- templates ---------------------------------------------------------------------------

    def templates(self, client: HevyClient, *, max_age: float = 86400.0) -> dict[str, dict]:
        """Every exercise template keyed by id; refetched from Hevy when none are stored or they are too old."""
        with self._lock, self._db(client) as db:
            now = self._clock()
            fetched = _meta(db, "templates_fetched_at")
            if fetched is None or now - float(fetched) > max_age:
                items = list(client.paginate("/v1/exercise_templates", "exercise_templates",
                                             page_size=TEMPLATE_PAGE_SIZE, ttl=0))
                db.execute("DELETE FROM templates")
                for template in items:
                    _put_template_row(db, template)
                _set_meta_values(db, templates_fetched_at=str(now))
            rows = db.execute("SELECT id, body FROM templates").fetchall()
        return {template_id: json.loads(body) for template_id, body in rows}

    def put_template(self, client: HevyClient, template: dict) -> None:
        with self._lock, self._db(client) as db:
            _put_template_row(db, template)

    # -- maintenance -------------------------------------------------------------------------

    def clear(self, client: HevyClient) -> None:
        """Drop every cached row and the sync state, so the next sync is a full one."""
        with self._lock, self._db(client) as db:
            for table in ("workouts", "templates", "meta"):
                db.execute(f"DELETE FROM {table}")

    # -- storage -----------------------------------------------------------------------------

    @contextmanager
    def _db(self, client: HevyClient) -> Iterator[sqlite3.Connection]:
        """One connection per call; the body runs in one transaction (committed, or rolled back on error)."""
        directory = self._data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"cache-{client.fingerprint()[:16]}.sqlite3"
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(SCHEMA)
            with conn:
                yield conn


def _upsert(db: sqlite3.Connection, workout: dict) -> None:
    db.execute("INSERT OR REPLACE INTO workouts (id, start_time, body) VALUES (?, ?, ?)",
               (str(workout["id"]), workout.get("start_time"), json.dumps(workout, ensure_ascii=False)))


def _put_template_row(db: sqlite3.Connection, template: dict) -> None:
    db.execute("INSERT OR REPLACE INTO templates (id, body) VALUES (?, ?)",
               (str(template["id"]), json.dumps(template, ensure_ascii=False)))


def _count(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]


def _meta(db: sqlite3.Connection, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(db: sqlite3.Connection, synced_at: float, synced_since: str) -> None:
    _set_meta_values(db, synced_at=str(synced_at), synced_since=synced_since)


def _set_meta_values(db: sqlite3.Connection, **values: Any) -> None:
    db.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", list(values.items()))
