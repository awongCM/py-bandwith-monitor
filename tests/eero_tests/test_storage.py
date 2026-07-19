from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from eero_monitor.models import (
    AGGREGATE_DEVICE,
    AggregateDeviceRates,
    DeviceRates,
    DeviceSnapshot,
    HealthEvent,
)
from eero_monitor.storage import MetricsDatabase


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "eero.db"
        self.db = MetricsDatabase(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        self._tmpdir.cleanup()

    def test_insert_and_history_by_device(self) -> None:
        now = time.time()
        sample = AggregateDeviceRates(
            timestamp=now,
            recv_bps=30.0,
            sent_bps=10.0,
            devices=(
                DeviceRates("a", "A", now, 20.0, 5.0, True),
                DeviceRates("b", "B", now, 10.0, 5.0, False),
            ),
        )
        self.db.insert_rates(sample)
        total = self.db.get_rate_history(AGGREGATE_DEVICE, minutes=5)
        self.assertEqual(len(total), 1)
        self.assertEqual(total[0]["recv_bps"], 30.0)
        device_a = self.db.get_rate_history("a", minutes=5)
        self.assertEqual(device_a[0]["sent_bps"], 5.0)

    def test_snapshots_and_overview(self) -> None:
        now = time.time()
        self.db.insert_rates(
            AggregateDeviceRates(
                timestamp=now,
                recv_bps=1.0,
                sent_bps=2.0,
                devices=(DeviceRates("a", "Phone", now, 1.0, 2.0, True),),
            )
        )
        self.db.insert_device_snapshots(
            now,
            [
                DeviceSnapshot("a", "Phone", "aa", "1.2.3.4", True, "wifi"),
            ],
        )
        overview = self.db.get_overview(minutes=5)
        self.assertEqual(overview["latest"]["recv_bps"], 1.0)
        devices = self.db.get_latest_device_snapshots()
        self.assertEqual(devices[0]["name"], "Phone")

    def test_retention_purge(self) -> None:
        old = time.time() - (10 * 86400)
        now = time.time()
        self.db.insert_rates(
            AggregateDeviceRates(
                timestamp=old,
                recv_bps=1.0,
                sent_bps=1.0,
                devices=(),
            )
        )
        self.db.insert_rates(
            AggregateDeviceRates(
                timestamp=now,
                recv_bps=2.0,
                sent_bps=2.0,
                devices=(),
            )
        )
        self.db.insert_health_event(
            HealthEvent(old, "a", "offline", "warning", "gone")
        )
        removed = self.db.purge_older_than(days=7)
        self.assertGreaterEqual(removed, 1)
        history = self.db.get_rate_history(AGGREGATE_DEVICE, minutes=60 * 24 * 30)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["recv_bps"], 2.0)


if __name__ == "__main__":
    unittest.main()
