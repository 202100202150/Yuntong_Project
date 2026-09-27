from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import TrackEvent, TrackState


class EventStore:
    def __init__(self, path: str | Path, retention_days: int = 30) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS track_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id TEXT NOT NULL,
                timestamp_s REAL NOT NULL,
                state TEXT NOT NULL,
                object_class TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_events_time ON track_events(timestamp_s DESC)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS access_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_s REAL NOT NULL,
                client_host TEXT,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status_code INTEGER NOT NULL
            )
            """
        )
        self._connection.commit()

    def add(self, event: TrackEvent) -> None:
        payload = event.to_dict()
        with self._lock:
            self._connection.execute(
                "INSERT INTO track_events(track_id,timestamp_s,state,object_class,payload_json) VALUES(?,?,?,?,?)",
                (
                    event.track_id,
                    event.timestamp_s,
                    event.state.value,
                    event.object_class.value,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def list(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM track_events ORDER BY timestamp_s DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def cleanup(self, now_s: float | None = None) -> int:
        cutoff = (now_s or time.time()) - self.retention_days * 86400
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM track_events WHERE timestamp_s < ?", (cutoff,)
            )
            self._connection.execute(
                "DELETE FROM access_audit WHERE timestamp_s < ?", (cutoff,)
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def log_access(
        self,
        client_host: str | None,
        method: str,
        path: str,
        status_code: int,
    ) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO access_audit(timestamp_s,client_host,method,path,status_code) VALUES(?,?,?,?,?)",
                (time.time(), client_host, method, path, status_code),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def should_persist(event: TrackEvent) -> bool:
    return event.state == TrackState.LOST or bool(
        event.metadata.get("justConfirmed", False)
    )
