from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from eero_monitor.models import (
    AGGREGATE_DEVICE,
    AggregateDeviceRates,
    DeviceRates,
    DeviceSnapshot,
    HealthEvent,
)
from eero_monitor.server import create_app


class FakeClient:
    def list_device_samples(self):
        return []


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "eero.db"
        self.app = create_app(
            db_path=str(self.db_path),
            interval=60.0,
            client=FakeClient(),
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self._tmpdir.cleanup()

    def test_api_shapes(self) -> None:
        now = 1_000_000_000.0
        db = self.app.state.database
        db.insert_rates(
            AggregateDeviceRates(
                timestamp=now,
                recv_bps=40.0,
                sent_bps=10.0,
                devices=(DeviceRates("a", "Phone", now, 40.0, 10.0, True),),
            )
        )
        db.insert_device_snapshots(
            now,
            [DeviceSnapshot("a", "Phone", "aa:bb", "1.2.3.4", True, "wifi")],
        )
        db.insert_health_event(
            HealthEvent(now, "a", "online", "info", "Phone came online")
        )

        overview = self.client.get("/api/overview?minutes=5")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["latest"]["recv_bps"], 40.0)

        history = self.client.get(
            f"/api/history?device={AGGREGATE_DEVICE}&minutes=15"
        )
        self.assertEqual(history.status_code, 200)
        self.assertIn("samples", history.json())

        devices = self.client.get("/api/devices")
        self.assertEqual(devices.status_code, 200)
        payload = devices.json()
        self.assertEqual(payload["snapshots"][0]["name"], "Phone")
        self.assertEqual(payload["rates"][0]["device_id"], "a")

        health = self.client.get("/api/health?limit=10")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["events"][0]["event_type"], "online")

    def test_dashboard_html(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Household", response.text)

    def test_websocket_hello(self) -> None:
        self.app.state.database.insert_rates(
            AggregateDeviceRates(
                timestamp=1_000_000_000.0,
                recv_bps=1.0,
                sent_bps=2.0,
                devices=(),
            )
        )
        with self.client.websocket_connect("/ws/live") as websocket:
            payload = websocket.receive_json()
        self.assertEqual(payload["type"], "hello")
        self.assertIn("latest", payload)


if __name__ == "__main__":
    unittest.main()
