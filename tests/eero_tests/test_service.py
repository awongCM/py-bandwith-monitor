from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from eero_monitor.models import DeviceSnapshot
from eero_monitor.service import SamplingService
from eero_monitor.storage import MetricsDatabase


class FakeClient:
    def list_device_samples(self):
        return [
            (
                DeviceSnapshot("a", "Phone", "aa", "1.2.3.4", True, "wifi"),
                100.0,
                50.0,
            )
        ]


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


if __name__ == "__main__":
    unittest.main()
