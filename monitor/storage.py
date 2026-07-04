"""SQLite persistence for bandwidth samples and health events."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from monitor.models import (
    AGGREGATE_INTERFACE,
    AggregateRates,
    HealthEvent,
    InterfaceStats,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rate_samples_ts
    ON rate_samples(timestamp);

CREATE INDEX IF NOT EXISTS idx_rate_samples_iface_ts
    ON rate_samples(interface, timestamp);

CREATE TABLE IF NOT EXISTS interface_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    name TEXT NOT NULL,
    is_up INTEGER NOT NULL,
    speed_mbps INTEGER NOT NULL,
    duplex TEXT NOT NULL,
    mtu INTEGER NOT NULL,
    bytes_recv INTEGER NOT NULL,
    bytes_sent INTEGER NOT NULL,
    packets_recv INTEGER NOT NULL,
    packets_sent INTEGER NOT NULL,
    errin INTEGER NOT NULL,
    errout INTEGER NOT NULL,
    dropin INTEGER NOT NULL,
    dropout INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interface_snapshots_ts
    ON interface_snapshots(timestamp);

CREATE INDEX IF NOT EXISTS idx_interface_snapshots_name_ts
    ON interface_snapshots(name, timestamp);

CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    interface TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);

CREATE INDEX IF NOT EXISTS idx_health_events_ts
    ON health_events(timestamp DESC);
"""


class MetricsDatabase:
    """Thread-safe SQLite store for samples, snapshots, and health events."""

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

    def insert_rates(self, sample: AggregateRates) -> None:
        rows = [
            (
                sample.timestamp,
                AGGREGATE_INTERFACE,
                sample.recv_bps,
                sample.sent_bps,
                0.0,
                0.0,
            )
        ]
        for interface in sample.interfaces:
            rows.append(
                (
                    interface.timestamp,
                    interface.name,
                    interface.recv_bps,
                    interface.sent_bps,
                    interface.recv_pps,
                    interface.sent_pps,
                )
            )

        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO rate_samples (
                    timestamp, interface, recv_bps, sent_bps, recv_pps, sent_pps
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    def insert_interface_snapshots(
        self,
        timestamp: float,
        interfaces: Iterable[InterfaceStats],
    ) -> None:
        rows = [
            (
                timestamp,
                item.name,
                int(item.is_up),
                item.speed_mbps,
                item.duplex,
                item.mtu,
                item.bytes_recv,
                item.bytes_sent,
                item.packets_recv,
                item.packets_sent,
                item.errin,
                item.errout,
                item.dropin,
                item.dropout,
            )
            for item in interfaces
        ]
        if not rows:
            return

        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO interface_snapshots (
                    timestamp, name, is_up, speed_mbps, duplex, mtu,
                    bytes_recv, bytes_sent, packets_recv, packets_sent,
                    errin, errout, dropin, dropout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    def insert_health_event(self, event: HealthEvent) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO health_events (
                    timestamp, interface, event_type, severity, message, value
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp,
                    event.interface,
                    event.event_type,
                    event.severity,
                    event.message,
                    event.value,
                ),
            )
            self._conn.commit()

    def get_rate_history(
        self,
        interface: str,
        *,
        minutes: float,
    ) -> list[dict[str, Any]]:
        since = time.time() - (minutes * 60)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, recv_bps, sent_bps, recv_pps, sent_pps
                FROM rate_samples
                WHERE interface = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (interface, since),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_rates(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT timestamp, recv_bps, sent_bps
                FROM rate_samples
                WHERE interface = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (AGGREGATE_INTERFACE,),
            ).fetchone()
        return dict(row) if row else None

    def get_latest_interface_rates(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT rs.interface AS name, rs.timestamp, rs.recv_bps, rs.sent_bps
                FROM rate_samples rs
                INNER JOIN (
                    SELECT interface, MAX(timestamp) AS max_ts
                    FROM rate_samples
                    WHERE interface != ?
                    GROUP BY interface
                ) latest
                ON rs.interface = latest.interface
                AND rs.timestamp = latest.max_ts
                ORDER BY rs.interface ASC
                """,
                (AGGREGATE_INTERFACE,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_interface_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.*
                FROM interface_snapshots s
                INNER JOIN (
                    SELECT name, MAX(timestamp) AS max_ts
                    FROM interface_snapshots
                    GROUP BY name
                ) latest
                ON s.name = latest.name AND s.timestamp = latest.max_ts
                ORDER BY s.name ASC
                """,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_health_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, interface, event_type, severity, message, value
                FROM health_events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_overview(self, *, minutes: float = 5) -> dict[str, Any]:
        latest = self.get_latest_rates()
        history = self.get_rate_history(AGGREGATE_INTERFACE, minutes=minutes)
        interfaces = self.get_latest_interface_rates()
        return {
            "latest": latest,
            "history": history,
            "interfaces": interfaces,
            "minutes": minutes,
        }

    def prune_old_data(self, *, days: int = 7) -> None:
        cutoff = time.time() - (days * 86400)
        with self._lock:
            self._conn.execute(
                "DELETE FROM rate_samples WHERE timestamp < ?",
                (cutoff,),
            )
            self._conn.execute(
                "DELETE FROM interface_snapshots WHERE timestamp < ?",
                (cutoff,),
            )
            self._conn.execute(
                "DELETE FROM health_events WHERE timestamp < ?",
                (cutoff,),
            )
            self._conn.commit()
