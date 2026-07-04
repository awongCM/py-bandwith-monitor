from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from monitor.health import HealthMonitor
from monitor.models import (
    AGGREGATE_INTERFACE,
    AggregateRates,
    HealthEvent,
    InterfaceRates,
    InterfaceStats,
)
from monitor.storage import MetricsDatabase


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = MetricsDatabase(Path(self.tempdir.name) / "test.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tempdir.cleanup()

    def test_insert_and_query_rates(self) -> None:
        sample = AggregateRates(
            timestamp=time.time(),
            recv_bps=1000.0,
            sent_bps=2000.0,
            interfaces=(
                InterfaceRates(
                    name="eth0",
                    timestamp=time.time(),
                    recv_bps=1000.0,
                    sent_bps=2000.0,
                    recv_pps=1.0,
                    sent_pps=2.0,
                ),
            ),
        )
        self.db.insert_rates(sample)
        latest = self.db.get_latest_rates()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["recv_bps"], 1000.0)

        history = self.db.get_rate_history(AGGREGATE_INTERFACE, minutes=5)
        self.assertEqual(len(history), 1)

    def test_interface_snapshots_and_health(self) -> None:
        now = time.time()
        self.db.insert_interface_snapshots(
            now,
            [
                InterfaceStats(
                    name="eth0",
                    is_up=True,
                    speed_mbps=1000,
                    duplex="full",
                    mtu=1500,
                    bytes_recv=100,
                    bytes_sent=200,
                    packets_recv=1,
                    packets_sent=2,
                    errin=0,
                    errout=0,
                    dropin=0,
                    dropout=0,
                )
            ],
        )
        self.db.insert_health_event(
            HealthEvent(
                timestamp=now,
                interface="eth0",
                event_type="link_up",
                severity="info",
                message="eth0 link came up",
            )
        )
        snapshots = self.db.get_latest_interface_snapshots()
        self.assertEqual(len(snapshots), 1)
        events = self.db.get_health_events(limit=5)
        self.assertEqual(len(events), 1)


class HealthMonitorTests(unittest.TestCase):
    def test_link_down_event(self) -> None:
        monitor = HealthMonitor()
        interfaces = [
            InterfaceStats(
                name="eth0",
                is_up=True,
                speed_mbps=1000,
                duplex="full",
                mtu=1500,
                bytes_recv=0,
                bytes_sent=0,
                packets_recv=0,
                packets_sent=0,
                errin=0,
                errout=0,
                dropin=0,
                dropout=0,
            )
        ]
        monitor.evaluate(1.0, interfaces)
        interfaces[0] = InterfaceStats(
            name="eth0",
            is_up=False,
            speed_mbps=1000,
            duplex="full",
            mtu=1500,
            bytes_recv=0,
            bytes_sent=0,
            packets_recv=0,
            packets_sent=0,
            errin=0,
            errout=0,
            dropin=0,
            dropout=0,
        )
        events = monitor.evaluate(2.0, interfaces)
        self.assertEqual(events[0].event_type, "link_down")


if __name__ == "__main__":
    unittest.main()
