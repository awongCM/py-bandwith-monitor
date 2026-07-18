from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from monitor.alerts_settings import AlertSettings
from monitor.server import create_app


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "ingest.db")
        self.app = create_app(
            db_path=self.db_path,
            agent_token="secret",
            interval=60.0,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.database.close()
        self.tempdir.cleanup()

    def _payload(self, host_id: str = "laptop") -> dict:
        now = time.time()
        return {
            "host_id": host_id,
            "timestamp": now,
            "recv_bps": 1000.0,
            "sent_bps": 200.0,
            "interfaces": [
                {
                    "name": "en0",
                    "timestamp": now,
                    "recv_bps": 1000.0,
                    "sent_bps": 200.0,
                    "recv_pps": 1.0,
                    "sent_pps": 1.0,
                }
            ],
            "snapshots": [],
        }

    def test_rejects_missing_token(self) -> None:
        response = self.client.post("/api/agents/samples", json=self._payload())

        self.assertEqual(response.status_code, 401)

    def test_rejects_when_token_not_configured(self) -> None:
        app = create_app(db_path=self.db_path + ".notoken", agent_token=None)
        client = TestClient(app)
        response = client.post(
            "/api/agents/samples",
            headers={"Authorization": "Bearer secret"},
            json=self._payload(),
        )
        client.close()
        app.state.database.close()

        self.assertEqual(response.status_code, 503)

    def test_accepts_valid_sample(self) -> None:
        response = self.client.post(
            "/api/agents/samples",
            headers={"Authorization": "Bearer secret"},
            json=self._payload("laptop"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        overview = self.client.get("/api/overview?host=laptop").json()
        self.assertEqual(overview["latest"]["recv_bps"], 1000.0)

    def test_rejects_empty_host_id(self) -> None:
        body = self._payload()
        body["host_id"] = "  "

        response = self.client.post(
            "/api/agents/samples",
            headers={"Authorization": "Bearer secret"},
            json=body,
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_malformed_json(self) -> None:
        response = self.client.post(
            "/api/agents/samples",
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
            },
            content=b"{not json",
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_non_object_json(self) -> None:
        response = self.client.post(
            "/api/agents/samples",
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
            },
            content=b"[1, 2, 3]",
        )

        self.assertEqual(response.status_code, 400)

    def test_evaluates_alerts_for_ingested_sample(self) -> None:
        app = create_app(
            db_path=self.db_path + ".alerts",
            agent_token="secret",
            interval=60.0,
            alert_settings=AlertSettings(
                recv_bps_threshold=900.0,
                bandwidth_sustained_seconds=0.0,
            ),
        )
        client = TestClient(app)
        response = client.post(
            "/api/agents/samples",
            headers={"Authorization": "Bearer secret"},
            json=self._payload("laptop"),
        )
        alerts = client.get("/api/alerts?host=laptop").json()["events"]
        client.close()
        app.state.database.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(alerts[0]["alert_type"], "recv_high")


if __name__ == "__main__":
    unittest.main()
