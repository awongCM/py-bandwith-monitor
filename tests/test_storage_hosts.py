from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from monitor.models import (
    AGGREGATE_INTERFACE,
    LOCAL_HOST_ID,
    AggregateRates,
    HealthEvent,
    InterfaceRates,
)
from monitor.storage import MetricsDatabase


LEGACY_SCHEMA = """
CREATE TABLE rate_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL
);
CREATE TABLE rate_samples_minute (
    bucket_start REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, interface)
);
CREATE TABLE rate_samples_hourly (
    bucket_start REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, interface)
);
CREATE TABLE rate_samples_daily (
    bucket_start REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, interface)
);
CREATE TABLE interface_snapshots (
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
CREATE TABLE health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    interface TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);
CREATE TABLE alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    rule_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    interface TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL,
    threshold REAL
);
"""


class HostIdStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "legacy.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _seed_legacy(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            """
            INSERT INTO rate_samples (
                timestamp, interface, recv_bps, sent_bps, recv_pps, sent_pps
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (time.time(), AGGREGATE_INTERFACE, 100.0, 50.0, 0.0, 0.0),
        )
        conn.commit()
        conn.close()

    def test_migrates_legacy_rows_to_local(self) -> None:
        self._seed_legacy()
        db = MetricsDatabase(self.path)
        try:
            latest = db.get_latest_rates(host_id=LOCAL_HOST_ID)
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest["recv_bps"], 100.0)
            cols = {
                row[1]
                for row in db._conn.execute("PRAGMA table_info(rate_samples)").fetchall()
            }
            self.assertIn("host_id", cols)
        finally:
            db.close()

    def test_host_isolation(self) -> None:
        db = MetricsDatabase(Path(self.tempdir.name) / "multi.db")
        try:
            now = time.time()
            local = AggregateRates(
                timestamp=now,
                recv_bps=10.0,
                sent_bps=1.0,
                interfaces=(
                    InterfaceRates("eth0", now, 10.0, 1.0, 1.0, 1.0),
                ),
            )
            remote = AggregateRates(
                timestamp=now,
                recv_bps=99.0,
                sent_bps=9.0,
                interfaces=(
                    InterfaceRates("en0", now, 99.0, 9.0, 1.0, 1.0),
                ),
            )
            db.insert_rates(local, host_id=LOCAL_HOST_ID)
            db.insert_rates(remote, host_id="laptop")
            self.assertEqual(db.get_latest_rates(host_id=LOCAL_HOST_ID)["recv_bps"], 10.0)
            self.assertEqual(db.get_latest_rates(host_id="laptop")["recv_bps"], 99.0)
            hosts = {item["host_id"] for item in db.list_hosts()}
            self.assertEqual(hosts, {LOCAL_HOST_ID, "laptop"})
        finally:
            db.close()

    def test_recovers_partial_host_id_migration(self) -> None:
        """rate_samples already has host_id but a rollup still uses the old PK."""
        now = time.time()
        bucket = float(int(now // 60) * 60)
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE rate_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                host_id TEXT NOT NULL,
                interface TEXT NOT NULL,
                recv_bps REAL NOT NULL,
                sent_bps REAL NOT NULL,
                recv_pps REAL NOT NULL,
                sent_pps REAL NOT NULL
            );
            CREATE TABLE rate_samples_minute (
                bucket_start REAL NOT NULL,
                interface TEXT NOT NULL,
                recv_bps REAL NOT NULL,
                sent_bps REAL NOT NULL,
                recv_pps REAL NOT NULL,
                sent_pps REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (bucket_start, interface)
            );
            CREATE TABLE rate_samples_hourly (
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
            CREATE TABLE rate_samples_daily (
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
            CREATE TABLE interface_snapshots (
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
            CREATE TABLE health_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                host_id TEXT NOT NULL,
                interface TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                value REAL
            );
            CREATE TABLE alert_events (
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
            """
        )
        conn.execute(
            """
            INSERT INTO rate_samples (
                timestamp, host_id, interface, recv_bps, sent_bps, recv_pps, sent_pps
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (now, LOCAL_HOST_ID, AGGREGATE_INTERFACE, 100.0, 50.0, 0.0, 0.0),
        )
        conn.execute(
            """
            INSERT INTO rate_samples_minute (
                bucket_start, interface, recv_bps, sent_bps, recv_pps, sent_pps, sample_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (bucket, AGGREGATE_INTERFACE, 100.0, 50.0, 0.0, 0.0, 1),
        )
        conn.commit()
        conn.close()

        db = MetricsDatabase(self.path)
        try:
            minute_cols = {
                row[1]
                for row in db._conn.execute(
                    "PRAGMA table_info(rate_samples_minute)"
                ).fetchall()
            }
            self.assertIn("host_id", minute_cols)
            latest = db.get_latest_rates(host_id=LOCAL_HOST_ID)
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest["recv_bps"], 100.0)
            rows = db.get_rate_history(
                AGGREGATE_INTERFACE,
                minutes=60,
                resolution="minute",
                host_id=LOCAL_HOST_ID,
            )
            self.assertTrue(rows)
            self.assertEqual(rows[0]["recv_bps"], 100.0)
        finally:
            db.close()

    def test_recovers_orphaned_rollup_staging(self) -> None:
        """Crash after DROP left data only in *_host_migrate; SCHEMA made empty live table."""
        now = time.time()
        bucket = float(int(now // 60) * 60)
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE rate_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                host_id TEXT NOT NULL,
                interface TEXT NOT NULL,
                recv_bps REAL NOT NULL,
                sent_bps REAL NOT NULL,
                recv_pps REAL NOT NULL,
                sent_pps REAL NOT NULL
            );
            CREATE TABLE rate_samples_minute (
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
            CREATE TABLE rate_samples_minute_host_migrate (
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
            CREATE TABLE rate_samples_hourly (
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
            CREATE TABLE rate_samples_daily (
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
            """
        )
        conn.execute(
            """
            INSERT INTO rate_samples_minute_host_migrate (
                bucket_start, host_id, interface, recv_bps, sent_bps,
                recv_pps, sent_pps, sample_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bucket, LOCAL_HOST_ID, AGGREGATE_INTERFACE, 77.0, 11.0, 0.0, 0.0, 1),
        )
        conn.commit()
        conn.close()

        db = MetricsDatabase(self.path)
        try:
            staging = db._conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'rate_samples_minute_host_migrate'
                """
            ).fetchone()
            self.assertIsNone(staging)
            rows = db.get_rate_history(
                AGGREGATE_INTERFACE,
                minutes=60,
                resolution="minute",
                host_id=LOCAL_HOST_ID,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["recv_bps"], 77.0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
