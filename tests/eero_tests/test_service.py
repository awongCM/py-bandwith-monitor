from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from eero_monitor.models import DeviceSnapshot
from eero_monitor.service import SamplingService
from eero_monitor.storage import MetricsDatabase


class FakeClient:
    def __init__(self, samples=None, *, fail_once: bool = False):
        self._samples = samples or [
            (
                DeviceSnapshot("a", "Phone", "aa", "1.2.3.4", True, "wifi"),
                100.0,
                50.0,
            )
        ]
        self._fail_once = fail_once
        self._calls = 0

    def list_device_samples(self):
        self._calls += 1
        if self._fail_once and self._calls == 1:
            raise RuntimeError("temporary failure")
        return self._samples


class ServiceTests(unittest.TestCase):
    def test_sample_once_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = MetricsDatabase(Path(tmp) / "eero.db")
            service = SamplingService(
                db,
                client=FakeClient(),
                interval=5.0,
                retention_days=7,
            )
            service.sample_once()
            overview = db.get_overview(minutes=5)
            self.assertEqual(overview["latest"]["recv_bps"], 100.0)
            devices = db.get_latest_device_snapshots()
            self.assertEqual(devices[0]["device_id"], "a")
            rates = db.get_latest_device_rates()
            self.assertEqual(rates[0]["sent_bps"], 50.0)
            db.close()

    def test_clears_api_health_events_after_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = MetricsDatabase(Path(tmp) / "eero.db")
            service = SamplingService(
                db,
                client=FakeClient(fail_once=True),
                interval=5.0,
                retention_days=7,
            )
            service.sample_once()
            self.assertEqual(len(db.get_health_events(limit=10)), 1)
            service.sample_once()
            self.assertEqual(db.get_health_events(limit=10), [])
            db.close()


if __name__ == "__main__":
    unittest.main()
