"""Integration tests for the background sampler and HTTP API."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil
from fastapi.testclient import TestClient

from monitor.server import create_app
from monitor.service import SamplingService
from monitor.storage import MetricsDatabase


def _mock_nic_stats() -> dict[str, object]:
    return {
        "eth0": type(
            "Stats",
            (),
            {
                "isup": True,
                "speed": 1000,
                "duplex": psutil.NIC_DUPLEX_FULL,
                "mtu": 1500,
            },
        )()
    }


def _mock_counter_factory(
    *,
    start_recv: int = 1_000,
    start_sent: int = 2_000,
    step: int = 500,
):
    counter = type(
        "Counters",
        (),
        {
            "bytes_recv": start_recv,
            "bytes_sent": start_sent,
            "packets_recv": 10,
            "packets_sent": 20,
            "errin": 0,
            "errout": 0,
            "dropin": 0,
            "dropout": 0,
        },
    )

    def next_counters(**kwargs: object) -> dict[str, object]:
        counter.bytes_recv += step
        counter.bytes_sent += step
        counter.packets_recv += 1
        counter.packets_sent += 1
        return {"eth0": counter}

    return next_counters


class SamplerIntegrationTests(unittest.TestCase):
    @patch("monitor.collector.psutil.net_io_counters")
    @patch("monitor.collector.psutil.net_if_stats")
    def test_sampler_persists_rates_and_snapshots(
        self,
        mock_stats,
        mock_counters,
    ) -> None:
        mock_stats.return_value = _mock_nic_stats()
        mock_counters.side_effect = _mock_counter_factory()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "integration.db"
            database = MetricsDatabase(db_path)
            service = SamplingService(
                database,
                interval=0.02,
                history_size=10,
                include=("eth0",),
            )

            try:
                service.start()
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    latest = database.get_latest_rates()
                    if latest is not None and latest["recv_bps"] > 0:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("sampler did not persist rate samples in time")

                snapshots = database.get_latest_interface_snapshots()
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0]["name"], "eth0")
            finally:
                service.stop()
                database.close()


class ApiSamplerIntegrationTests(unittest.TestCase):
    @patch("monitor.collector.psutil.net_io_counters")
    @patch("monitor.collector.psutil.net_if_stats")
    def test_api_returns_data_after_background_sampling(
        self,
        mock_stats,
        mock_counters,
    ) -> None:
        mock_stats.return_value = _mock_nic_stats()
        mock_counters.side_effect = _mock_counter_factory()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "api-integration.db"
            app = create_app(
                db_path=str(db_path),
                interval=0.02,
                history_size=10,
                include=("eth0",),
            )

            with TestClient(app) as client:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    overview = client.get("/api/overview?minutes=5")
                    self.assertEqual(overview.status_code, 200)
                    if overview.json()["history"]:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("API did not receive sampler history in time")

                interfaces = client.get("/api/interfaces")
                self.assertEqual(interfaces.status_code, 200)
                payload = interfaces.json()
                self.assertEqual(len(payload["snapshots"]), 1)
                self.assertEqual(payload["snapshots"][0]["name"], "eth0")
                self.assertTrue(payload["rates"])

                history = client.get("/api/history?interface=eth0&minutes=5")
                self.assertEqual(history.status_code, 200)
                self.assertGreaterEqual(len(history.json()["samples"]), 1)


if __name__ == "__main__":
    unittest.main()
