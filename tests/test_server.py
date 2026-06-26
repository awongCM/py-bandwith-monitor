from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

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


if __name__ == "__main__":
    unittest.main()
