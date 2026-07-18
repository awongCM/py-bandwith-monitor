"""SQLite persistence for bandwidth samples and health events."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Literal

from monitor.models import (
    AGGREGATE_INTERFACE,
    LOCAL_HOST_ID,
    AggregateRates,
    AlertEvent,
    HealthEvent,
    InterfaceStats,
)
from monitor.retention import RetentionSettings

Resolution = Literal["raw", "minute", "hour", "day", "auto"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    host_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rate_samples_host_ts
    ON rate_samples(host_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_rate_samples_host_iface_ts
    ON rate_samples(host_id, interface, timestamp);

CREATE TABLE IF NOT EXISTS rate_samples_minute (
    bucket_start REAL NOT NULL,
    host_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, host_id, interface)
);

CREATE INDEX IF NOT EXISTS idx_rate_samples_minute_host_iface_ts
    ON rate_samples_minute(host_id, interface, bucket_start);

CREATE TABLE IF NOT EXISTS rate_samples_hourly (
    bucket_start REAL NOT NULL,
    host_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, host_id, interface)
);

CREATE INDEX IF NOT EXISTS idx_rate_samples_hourly_host_iface_ts
    ON rate_samples_hourly(host_id, interface, bucket_start);

CREATE TABLE IF NOT EXISTS rate_samples_daily (
    bucket_start REAL NOT NULL,
    host_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, host_id, interface)
);

CREATE INDEX IF NOT EXISTS idx_rate_samples_daily_host_iface_ts
    ON rate_samples_daily(host_id, interface, bucket_start);

CREATE TABLE IF NOT EXISTS interface_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    host_id TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_interface_snapshots_host_ts
    ON interface_snapshots(host_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_interface_snapshots_host_name_ts
    ON interface_snapshots(host_id, name, timestamp);

CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    host_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);

CREATE INDEX IF NOT EXISTS idx_health_events_host_ts
    ON health_events(host_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    host_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    interface TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL,
    threshold REAL
);

CREATE INDEX IF NOT EXISTS idx_alert_events_host_ts
    ON alert_events(host_id, timestamp DESC);
"""

_MINUTE_SECONDS = 60
_HOUR_SECONDS = 3600
_DAY_SECONDS = 86400


def _floor_bucket(timestamp: float, bucket_seconds: int) -> float:
    return float(int(timestamp // bucket_seconds) * bucket_seconds)


def choose_resolution(minutes: float, resolution: Resolution = "auto") -> str:
    if resolution != "auto":
        return resolution
    if minutes <= 180:
        return "raw"
    if minutes <= 30 * 24 * 60:
        return "minute"
    if minutes <= 90 * 24 * 60:
        return "hour"
    return "day"


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
            if self._table_columns("rate_samples") and (
                "host_id" not in self._table_columns("rate_samples")
            ):
                self._migrate_host_id_locked()
            self._conn.executescript(SCHEMA)
            self._migrate_host_id_locked()
            self._conn.commit()

    def _table_columns(self, table: str) -> set[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in rows}

    def _migrate_host_id_locked(self) -> None:
        if "host_id" in self._table_columns("rate_samples"):
            return
        for table in (
            "rate_samples",
            "interface_snapshots",
            "health_events",
            "alert_events",
        ):
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN host_id TEXT NOT NULL DEFAULT '{LOCAL_HOST_ID}'"
            )
        for table in (
            "rate_samples_minute",
            "rate_samples_hourly",
            "rate_samples_daily",
        ):
            self._rebuild_rollup_with_host_id_locked(table)

    def _rebuild_rollup_with_host_id_locked(self, table: str) -> None:
        staging = f"{table}_host_migrate"
        self._conn.execute(f"DROP TABLE IF EXISTS {staging}")
        self._conn.execute(
            f"""
            CREATE TABLE {staging} (
                bucket_start REAL NOT NULL,
                host_id TEXT NOT NULL,
                interface TEXT NOT NULL,
                recv_bps REAL NOT NULL,
                sent_bps REAL NOT NULL,
                recv_pps REAL NOT NULL,
                sent_pps REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (bucket_start, host_id, interface)
            )
            """
        )
        self._conn.execute(
            f"""
            INSERT INTO {staging} (
                bucket_start, host_id, interface, recv_bps, sent_bps,
                recv_pps, sent_pps, sample_count
            )
            SELECT
                bucket_start, ?, interface, recv_bps, sent_bps,
                recv_pps, sent_pps, sample_count
            FROM {table}
            """,
            (LOCAL_HOST_ID,),
        )
        self._conn.execute(f"DROP TABLE {table}")
        self._conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")
        self._conn.execute(
            f"""
            CREATE INDEX idx_{table}_host_iface_ts
                ON {table}(host_id, interface, bucket_start)
            """
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_rates(
        self, sample: AggregateRates, *, host_id: str = LOCAL_HOST_ID
    ) -> None:
        rows = [
            (
                sample.timestamp,
                host_id,
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
                    host_id,
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
                    timestamp, host_id, interface, recv_bps, sent_bps, recv_pps, sent_pps
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    def insert_interface_snapshots(
        self,
        timestamp: float,
        interfaces: Iterable[InterfaceStats],
        *,
        host_id: str = LOCAL_HOST_ID,
    ) -> None:
        rows = [
            (
                timestamp,
                host_id,
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
                    timestamp, host_id, name, is_up, speed_mbps, duplex, mtu,
                    bytes_recv, bytes_sent, packets_recv, packets_sent,
                    errin, errout, dropin, dropout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    def insert_health_event(self, event: HealthEvent) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO health_events (
                    timestamp, host_id, interface, event_type, severity, message, value
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp,
                    event.host_id,
                    event.interface,
                    event.event_type,
                    event.severity,
                    event.message,
                    event.value,
                ),
            )
            self._conn.commit()

    def insert_alert_event(self, event: AlertEvent) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO alert_events (
                    timestamp, host_id, rule_id, alert_type, severity, interface,
                    message, value, threshold
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp,
                    event.host_id,
                    event.rule_id,
                    event.alert_type,
                    event.severity,
                    event.interface,
                    event.message,
                    event.value,
                    event.threshold,
                ),
            )
            self._conn.commit()

    def get_rate_history(
        self,
        interface: str,
        *,
        minutes: float,
        resolution: Resolution = "auto",
        host_id: str = LOCAL_HOST_ID,
    ) -> list[dict[str, Any]]:
        now = time.time()
        since = now - (minutes * 60)
        tier = choose_resolution(minutes, resolution)
        if tier == "raw":
            return self._get_raw_rate_history(interface, since=since, host_id=host_id)
        if tier == "minute":
            return self._get_rollup_rate_history(
                "rate_samples_minute",
                interface,
                since=since,
                host_id=host_id,
            )
        if tier == "hour":
            return self._get_rollup_rate_history(
                "rate_samples_hourly",
                interface,
                since=since,
                host_id=host_id,
            )
        return self._get_rollup_rate_history(
            "rate_samples_daily",
            interface,
            since=since,
            host_id=host_id,
        )

    def _get_raw_rate_history(
        self,
        interface: str,
        *,
        since: float,
        host_id: str = LOCAL_HOST_ID,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, recv_bps, sent_bps, recv_pps, sent_pps
                FROM rate_samples
                WHERE host_id = ? AND interface = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (host_id, interface, since),
            ).fetchall()
        return [dict(row) for row in rows]

    def _get_rollup_rate_history(
        self,
        table: str,
        interface: str,
        *,
        since: float,
        host_id: str = LOCAL_HOST_ID,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    bucket_start AS timestamp,
                    recv_bps,
                    sent_bps,
                    recv_pps,
                    sent_pps,
                    sample_count
                FROM {table}
                WHERE host_id = ? AND interface = ? AND bucket_start >= ?
                ORDER BY bucket_start ASC
                """,
                (host_id, interface, since),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_rates(
        self, *, host_id: str = LOCAL_HOST_ID
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT timestamp, recv_bps, sent_bps
                FROM rate_samples
                WHERE host_id = ? AND interface = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (host_id, AGGREGATE_INTERFACE),
            ).fetchone()
        return dict(row) if row else None

    def get_latest_interface_rates(
        self, *, host_id: str = LOCAL_HOST_ID
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT rs.interface AS name, rs.timestamp, rs.recv_bps, rs.sent_bps
                FROM rate_samples rs
                INNER JOIN (
                    SELECT interface, MAX(timestamp) AS max_ts
                    FROM rate_samples
                    WHERE host_id = ? AND interface != ?
                    GROUP BY interface
                ) latest
                ON rs.interface = latest.interface
                AND rs.timestamp = latest.max_ts
                WHERE rs.host_id = ?
                ORDER BY rs.interface ASC
                """,
                (host_id, AGGREGATE_INTERFACE, host_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_interface_snapshots(
        self, *, host_id: str = LOCAL_HOST_ID
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.*
                FROM interface_snapshots s
                INNER JOIN (
                    SELECT name, MAX(timestamp) AS max_ts
                    FROM interface_snapshots
                    WHERE host_id = ?
                    GROUP BY name
                ) latest
                ON s.name = latest.name AND s.timestamp = latest.max_ts
                WHERE s.host_id = ?
                ORDER BY s.name ASC
                """,
                (host_id, host_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_health_events(
        self, *, limit: int = 50, host_id: str = LOCAL_HOST_ID
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, interface, event_type, severity, message, value
                FROM health_events
                WHERE host_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (host_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_alert_events(
        self, *, limit: int = 50, host_id: str = LOCAL_HOST_ID
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT timestamp, rule_id, alert_type, severity, interface,
                       message, value, threshold
                FROM alert_events
                WHERE host_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (host_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_overview(
        self,
        *,
        minutes: float = 5,
        resolution: Resolution = "auto",
        host_id: str = LOCAL_HOST_ID,
    ) -> dict[str, Any]:
        latest = self.get_latest_rates(host_id=host_id)
        tier = choose_resolution(minutes, resolution)
        history = self.get_rate_history(
            AGGREGATE_INTERFACE,
            minutes=minutes,
            resolution=resolution,
            host_id=host_id,
        )
        interfaces = self.get_latest_interface_rates(host_id=host_id)
        return {
            "latest": latest,
            "history": history,
            "interfaces": interfaces,
            "minutes": minutes,
            "resolution": tier,
        }

    def list_hosts(
        self, *, online_after_seconds: float = 30.0
    ) -> list[dict[str, Any]]:
        cutoff = time.time() - online_after_seconds
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT host_id, MAX(timestamp) AS last_seen
                FROM rate_samples
                WHERE interface = ?
                GROUP BY host_id
                ORDER BY host_id ASC
                """,
                (AGGREGATE_INTERFACE,),
            ).fetchall()
        return [
            {
                "host_id": row["host_id"],
                "last_seen": row["last_seen"],
                "online": row["last_seen"] >= cutoff,
            }
            for row in rows
        ]

    def rollup_raw_to_minute(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO rate_samples_minute (
                    bucket_start, host_id, interface, recv_bps, sent_bps,
                    recv_pps, sent_pps, sample_count
                )
                SELECT
                    CAST(timestamp / 60 AS INTEGER) * 60.0 AS bucket_start,
                    host_id,
                    interface,
                    AVG(recv_bps),
                    AVG(sent_bps),
                    AVG(recv_pps),
                    AVG(sent_pps),
                    COUNT(*)
                FROM rate_samples
                WHERE timestamp < ?
                GROUP BY bucket_start, host_id, interface
                ON CONFLICT(bucket_start, host_id, interface) DO UPDATE SET
                    recv_bps = excluded.recv_bps,
                    sent_bps = excluded.sent_bps,
                    recv_pps = excluded.recv_pps,
                    sent_pps = excluded.sent_pps,
                    sample_count = excluded.sample_count
                """,
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def rollup_minute_to_hourly(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO rate_samples_hourly (
                    bucket_start, host_id, interface, recv_bps, sent_bps,
                    recv_pps, sent_pps, sample_count
                )
                SELECT
                    CAST(bucket_start / 3600 AS INTEGER) * 3600.0 AS bucket_start,
                    host_id,
                    interface,
                    AVG(recv_bps),
                    AVG(sent_bps),
                    AVG(recv_pps),
                    AVG(sent_pps),
                    SUM(sample_count)
                FROM rate_samples_minute
                WHERE bucket_start < ?
                GROUP BY CAST(bucket_start / 3600 AS INTEGER), host_id, interface
                ON CONFLICT(bucket_start, host_id, interface) DO UPDATE SET
                    recv_bps = excluded.recv_bps,
                    sent_bps = excluded.sent_bps,
                    recv_pps = excluded.recv_pps,
                    sent_pps = excluded.sent_pps,
                    sample_count = excluded.sample_count
                """,
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def rollup_hourly_to_daily(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO rate_samples_daily (
                    bucket_start, host_id, interface, recv_bps, sent_bps,
                    recv_pps, sent_pps, sample_count
                )
                SELECT
                    CAST(bucket_start / 86400 AS INTEGER) * 86400.0 AS bucket_start,
                    host_id,
                    interface,
                    AVG(recv_bps),
                    AVG(sent_bps),
                    AVG(recv_pps),
                    AVG(sent_pps),
                    SUM(sample_count)
                FROM rate_samples_hourly
                WHERE bucket_start < ?
                GROUP BY CAST(bucket_start / 86400 AS INTEGER), host_id, interface
                ON CONFLICT(bucket_start, host_id, interface) DO UPDATE SET
                    recv_bps = excluded.recv_bps,
                    sent_bps = excluded.sent_bps,
                    recv_pps = excluded.recv_pps,
                    sent_pps = excluded.sent_pps,
                    sample_count = excluded.sample_count
                """,
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def prune_rate_samples(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rate_samples WHERE timestamp < ?",
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def prune_rate_samples_minute(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rate_samples_minute WHERE bucket_start < ?",
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def prune_rate_samples_hourly(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rate_samples_hourly WHERE bucket_start < ?",
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def prune_rate_samples_daily(self, *, before: float) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rate_samples_daily WHERE bucket_start < ?",
                (before,),
            )
            self._conn.commit()
            return cursor.rowcount

    def prune_auxiliary_data(self, *, before: float) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM interface_snapshots WHERE timestamp < ?",
                (before,),
            )
            self._conn.execute(
                "DELETE FROM health_events WHERE timestamp < ?",
                (before,),
            )
            self._conn.execute(
                "DELETE FROM alert_events WHERE timestamp < ?",
                (before,),
            )
            self._conn.commit()

    def run_retention_maintenance(
        self,
        settings: RetentionSettings | None = None,
        *,
        now: float | None = None,
    ) -> dict[str, int]:
        """Roll up completed buckets and prune each retention tier."""
        settings = settings or RetentionSettings()
        current = now if now is not None else time.time()
        current_minute = _floor_bucket(current, _MINUTE_SECONDS)
        current_hour = _floor_bucket(current, _HOUR_SECONDS)
        current_day = _floor_bucket(current, _DAY_SECONDS)

        minute_rows = self.rollup_raw_to_minute(before=current_minute)
        hourly_rows = self.rollup_minute_to_hourly(before=current_hour)
        daily_rows = self.rollup_hourly_to_daily(before=current_day)

        raw_cutoff = current - (settings.raw_retention_days * _DAY_SECONDS)
        minute_cutoff = current - (settings.minute_retention_days * _DAY_SECONDS)
        hourly_cutoff = current - (settings.hourly_retention_days * _DAY_SECONDS)
        daily_cutoff = current - (settings.daily_retention_days * _DAY_SECONDS)

        raw_deleted = self.prune_rate_samples(before=raw_cutoff)
        minute_deleted = self.prune_rate_samples_minute(before=minute_cutoff)
        hourly_deleted = self.prune_rate_samples_hourly(before=hourly_cutoff)
        daily_deleted = self.prune_rate_samples_daily(before=daily_cutoff)
        self.prune_auxiliary_data(before=raw_cutoff)

        return {
            "minute_upserts": minute_rows,
            "hourly_upserts": hourly_rows,
            "daily_upserts": daily_rows,
            "raw_deleted": raw_deleted,
            "minute_deleted": minute_deleted,
            "hourly_deleted": hourly_deleted,
            "daily_deleted": daily_deleted,
        }

    def prune_old_data(self, *, days: int = 7) -> None:
        """Backward-compatible prune hook for legacy callers."""
        settings = RetentionSettings(raw_retention_days=days)
        self.run_retention_maintenance(settings)
