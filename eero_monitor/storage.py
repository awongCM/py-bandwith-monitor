"""SQLite persistence for household device samples and health events."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from eero_monitor.models import (
    AGGREGATE_DEVICE,
    AggregateDeviceRates,
    DeviceSnapshot,
    HealthEvent,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    device_id TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eero_rate_device_ts
    ON rate_samples(device_id, timestamp);

CREATE TABLE IF NOT EXISTS device_snapshots (
    device_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    name TEXT NOT NULL,
    mac TEXT,
    ip TEXT,
    is_online INTEGER NOT NULL,
    connection TEXT NOT NULL,
    signal REAL,
    last_seen REAL
);

CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);

CREATE INDEX IF NOT EXISTS idx_eero_health_ts
    ON health_events(timestamp DESC);
"""


class MetricsDatabase:
    """Thread-safe SQLite store for eero_monitor samples and events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_rates(self, sample: AggregateDeviceRates) -> None:
        rows = [
            (
                sample.timestamp,
                AGGREGATE_DEVICE,
                sample.recv_bps,
                sample.sent_bps,
            )
        ]
        for device in sample.devices:
            rows.append(
                (
                    device.timestamp,
                    device.device_id,
                    device.recv_bps,
                    device.sent_bps,
                )
            )
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO rate_samples (
                    timestamp, device_id, recv_bps, sent_bps
                ) VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    def insert_device_snapshots(
        self,
        timestamp: float,
        devices: list[DeviceSnapshot],
    ) -> None:
        rows = [
            (
                device.device_id,
                timestamp,
                device.name,
                device.mac,
                device.ip,
                1 if device.is_online else 0,
                device.connection,
                device.signal,
                device.last_seen,
            )
            for device in devices
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO device_snapshots (
                    device_id, timestamp, name, mac, ip, is_online,
                    connection, signal, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    timestamp=excluded.timestamp,
                    name=excluded.name,
                    mac=excluded.mac,
                    ip=excluded.ip,
                    is_online=excluded.is_online,
                    connection=excluded.connection,
                    signal=excluded.signal,
                    last_seen=excluded.last_seen
                """,
                rows,
            )
            self._conn.commit()

    def insert_health_event(self, event: HealthEvent) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO health_events (
                    timestamp, device_id, event_type, severity, message, value
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp,
                    event.device_id,
                    event.event_type,
                    event.severity,
                    event.message,
                    event.value,
                ),
            )
            self._conn.commit()

    def get_rate_history(
        self,
        device_id: str,
        *,
        minutes: float,
    ) -> list[dict[str, Any]]:
        since = time.time() - (minutes * 60)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, device_id, recv_bps, sent_bps
                FROM rate_samples
                WHERE device_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (device_id, since),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_rates(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT timestamp, recv_bps, sent_bps
                FROM rate_samples
                WHERE device_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (AGGREGATE_DEVICE,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_latest_device_rates(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT r.timestamp, r.device_id, r.recv_bps, r.sent_bps,
                       COALESCE(s.name, r.device_id) AS name,
                       COALESCE(s.is_online, 0) AS is_online
                FROM rate_samples r
                LEFT JOIN device_snapshots s ON s.device_id = r.device_id
                WHERE r.device_id != ?
                  AND r.timestamp = (
                      SELECT MAX(timestamp) FROM rate_samples
                      WHERE device_id != ?
                  )
                ORDER BY name ASC
                """,
                (AGGREGATE_DEVICE, AGGREGATE_DEVICE),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["is_online"] = bool(item["is_online"])
            result.append(item)
        return result

    def get_latest_device_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT device_id, timestamp, name, mac, ip, is_online,
                       connection, signal, last_seen
                FROM device_snapshots
                ORDER BY name ASC
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["is_online"] = bool(item["is_online"])
            result.append(item)
        return result

    def get_health_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, device_id, event_type, severity, message, value
                FROM health_events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_overview(self, *, minutes: float = 5) -> dict[str, Any]:
        latest = self.get_latest_rates()
        history = self.get_rate_history(AGGREGATE_DEVICE, minutes=minutes)
        devices = self.get_latest_device_rates()
        return {
            "latest": latest,
            "history": history,
            "devices": devices,
            "minutes": minutes,
        }

    def purge_older_than(self, *, days: float) -> int:
        cutoff = time.time() - (days * 86400)
        with self._lock:
            cur1 = self._conn.execute(
                "DELETE FROM rate_samples WHERE timestamp < ?",
                (cutoff,),
            )
            cur2 = self._conn.execute(
                "DELETE FROM health_events WHERE timestamp < ?",
                (cutoff,),
            )
            self._conn.commit()
            return int(cur1.rowcount or 0) + int(cur2.rowcount or 0)
