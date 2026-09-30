"""Where events go. One sink interface, three implementations: in-memory
(tests), JSONL (simple/live-demo tail -f), sqlite (benchmark + web UI).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from .events import Event, _json_safe


class EventRecorder(Protocol):
    def record(self, event: Event) -> None: ...


class InMemoryRecorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def record(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


class JsonlRecorder:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: Event) -> None:
        line = json.dumps(event.to_dict())
        with self._lock:
            with self._path.open("a") as f:
                f.write(line + "\n")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    config TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp REAL NOT NULL,
    wall_time REAL NOT NULL,
    turn_id INTEGER NOT NULL,
    decision_id INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_scenario_config ON events(scenario_id, config);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    config TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    started_at REAL NOT NULL,
    response_latency_ms REAL,
    backchannel_latency_ms REAL,
    llm_ttft_ms REAL,
    tts_first_audio_ms REAL,
    stt_finalization_ms REAL,
    backchannel_count INTEGER NOT NULL DEFAULT 0,
    cancelled_count INTEGER NOT NULL DEFAULT 0,
    bad_backchannel_count INTEGER NOT NULL DEFAULT 0,
    suppressed_count INTEGER NOT NULL DEFAULT 0,
    overlap_ms REAL NOT NULL DEFAULT 0,
    notes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_scenario_config ON runs(scenario_id, config);

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    expected_behaviour TEXT NOT NULL
);
"""


class SqliteRecorder:
    """Thread-safe-enough for our use (one writer at a time via a lock);
    the benchmark runner is single-process and awaits each run to
    completion before starting the next."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record(self, event: Event) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO events
                   (run_id, scenario_id, config, event_type, source, timestamp, wall_time,
                    turn_id, decision_id, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.run_id,
                    event.scenario_id,
                    event.config,
                    event.event_type.value,
                    event.source,
                    event.timestamp,
                    event.wall_time,
                    event.turn_id,
                    event.decision_id,
                    json.dumps(_json_safe(event.metadata)),
                ),
            )
            self._conn.commit()

    def events_for_run(self, run_id: str) -> list[tuple[str, float, str]]:
        """(event_type, timestamp, metadata_json) for one run, in emission order."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT event_type, timestamp, metadata_json FROM events WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
            return cur.fetchall()

    def upsert_run(self, run_row: dict) -> None:
        cols = list(run_row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "run_id")
        with self._lock:
            self._conn.execute(
                f"""INSERT INTO runs ({", ".join(cols)}) VALUES ({placeholders})
                    ON CONFLICT(run_id) DO UPDATE SET {updates}""",
                [run_row[c] for c in cols],
            )
            self._conn.commit()

    def upsert_scenario(self, scenario_id: str, name: str, description: str, expected_behaviour: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO scenarios (scenario_id, name, description, expected_behaviour)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(scenario_id) DO UPDATE SET name=excluded.name,
                        description=excluded.description, expected_behaviour=excluded.expected_behaviour""",
                (scenario_id, name, description, expected_behaviour),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
