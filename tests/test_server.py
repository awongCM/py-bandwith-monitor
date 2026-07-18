from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from monitor.models import AggregateRates, InterfaceRates
from monitor.server import create_app


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(db_path=":memory:")
        self.client = TestClient(self.app)

    def test_overview_and_interfaces_endpoints(self) -> None:
        overview = self.client.get("/api/overview?minutes=5")
        self.assertEqual(overview.status_code, 200)
        payload = overview.json()
        self.assertIn("history", payload)

        interfaces = self.client.get("/api/interfaces")
        self.assertEqual(interfaces.status_code, 200)
        self.assertIn("snapshots", interfaces.json())

        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn("events", health.json())

    def test_overview_filters_by_host(self) -> None:
        timestamp = 1_000_000_000.0
        laptop = AggregateRates(
            timestamp=timestamp,
            recv_bps=200.0,
            sent_bps=20.0,
            interfaces=(
                InterfaceRates("en0", timestamp, 200.0, 20.0, 2.0, 1.0),
            ),
        )
        desktop = AggregateRates(
            timestamp=timestamp,
            recv_bps=100.0,
            sent_bps=10.0,
            interfaces=(
                InterfaceRates("eth0", timestamp, 100.0, 10.0, 1.0, 1.0),
            ),
        )
        self.app.state.database.insert_rates(laptop, host_id="laptop")
        self.app.state.database.insert_rates(desktop, host_id="desktop")

        response = self.client.get("/api/overview?host=laptop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latest"]["recv_bps"], 200.0)

    def test_list_hosts(self) -> None:
        timestamp = 1_000_000_000.0
        for host_id in ("laptop", "desktop"):
            self.app.state.database.insert_rates(
                AggregateRates(
                    timestamp=timestamp,
                    recv_bps=100.0,
                    sent_bps=10.0,
                    interfaces=(),
                ),
                host_id=host_id,
            )

        response = self.client.get("/api/hosts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {host["host_id"] for host in response.json()["hosts"]},
            {"desktop", "laptop"},
        )

    def test_alerts_endpoints(self) -> None:
        alerts = self.client.get("/api/alerts")
        self.assertEqual(alerts.status_code, 200)
        self.assertIn("events", alerts.json())

        status = self.client.get("/api/alerts/status")
        self.assertEqual(status.status_code, 200)
        payload = status.json()
        self.assertIn("bandwidth_enabled", payload)
        self.assertIn("webhook_configured", payload)


if __name__ == "__main__":
    unittest.main()
