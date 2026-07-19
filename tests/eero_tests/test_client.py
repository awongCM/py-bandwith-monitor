from __future__ import annotations

import time
import unittest
from typing import Any

from eero_monitor.client import EeroClient, _counter_delta_bps, _extract_usage_rates
from eero_monitor.models import DeviceSnapshot


class FakeTransport:
    def __init__(
        self,
        devices: list[dict[str, Any]],
        *,
        usage_totals: dict[str, tuple[int, int]] | None = None,
        usage_step: tuple[int, int] = (1_000_000, 100_000),
    ) -> None:
        self.devices = devices
        self.usage_totals = {
            device_id: [download, upload]
            for device_id, (download, upload) in (usage_totals or {}).items()
        }
        self.usage_step = usage_step

    def fetch_devices(self, network_id: str) -> list[dict[str, Any]]:
        assert network_id == "net1"
        return self.devices

    def fetch_usage_totals(self, network_id: str) -> dict[str, dict[str, Any]]:
        assert network_id == "net1"
        payload: dict[str, dict[str, Any]] = {}
        for device_id, values in self.usage_totals.items():
            values[0] += self.usage_step[0]
            values[1] += self.usage_step[1]
            payload[device_id] = {
                "download": values[0],
                "upload": values[1],
            }
        return payload


class ClientMappingTests(unittest.TestCase):
    def test_maps_devices_and_rates(self) -> None:
        transport = FakeTransport(
            [
                {
                    "url": "/2.2/devices/aaa",
                    "nickname": "Phone",
                    "hostname": "phone",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "ip": "192.168.1.10",
                    "connected": True,
                    "connection_type": "wireless",
                    "usage": {"down_mbps": 1.0, "up_mbps": 0.2},
                },
                {
                    "url": "/2.2/devices/bbb",
                    "hostname": "tv",
                    "mac": "11:22:33:44:55:66",
                    "connected": False,
                    "usage": {"down_mbps": 5.0, "up_mbps": 1.0},
                },
            ]
        )
        client = EeroClient("tok", "net1", transport=transport)
        samples = client.list_device_samples()
        self.assertEqual(len(samples), 2)
        phone, recv, sent = samples[0]
        self.assertIsInstance(phone, DeviceSnapshot)
        self.assertEqual(phone.device_id, "aaa")
        self.assertEqual(phone.name, "Phone")
        self.assertTrue(phone.is_online)
        self.assertEqual(phone.connection, "wifi")
        self.assertEqual(recv, 1_000_000.0)
        self.assertEqual(sent, 200_000.0)
        tv, recv2, sent2 = samples[1]
        self.assertEqual(tv.device_id, "bbb")
        self.assertEqual(tv.name, "tv")
        self.assertFalse(tv.is_online)
        self.assertEqual(recv2, 0.0)
        self.assertEqual(sent2, 0.0)

    def test_data_usage_fallback_when_live_usage_is_zero(self) -> None:
        transport = FakeTransport(
            [
                {
                    "url": "/2.2/devices/aaa",
                    "nickname": "Phone",
                    "connected": True,
                    "usage": {"down_mbps": 0, "up_mbps": 0},
                }
            ],
            usage_totals={"aaa": (0, 0)},
        )
        client = EeroClient("tok", "net1", transport=transport)
        client.list_device_samples()
        time.sleep(0.05)
        samples = client.list_device_samples()
        recv, sent = samples[0][1], samples[0][2]
        self.assertGreater(recv, 0.0)
        self.assertGreater(sent, 0.0)

    def test_skips_bad_device_entries(self) -> None:
        transport = FakeTransport(
            [
                {"url": "/2.2/devices/ok", "nickname": "Ok", "connected": True},
                None,  # type: ignore[list-item]
                {"connected": True},  # missing id
            ]
        )
        client = EeroClient("tok", "net1", transport=transport)
        samples = client.list_device_samples()
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0][0].device_id, "ok")

    def test_extract_usage_rates_from_mbps_fields(self) -> None:
        recv, sent = _extract_usage_rates(
            {"usage": {"down_mbps": 2.5, "up_mbps": 0.5}}
        )
        self.assertEqual(recv, 2_500_000.0)
        self.assertEqual(sent, 500_000.0)

    def test_counter_delta_bps_computes_rate(self) -> None:
        last: dict[str, tuple[float, float, float]] = {}
        _counter_delta_bps("dev1", 1.0, 0.0, 0.0, last)
        recv, sent = _counter_delta_bps("dev1", 2.0, 1_000_000.0, 100_000.0, last)
        self.assertEqual(recv, 8_000_000.0)
        self.assertEqual(sent, 800_000.0)


if __name__ == "__main__":
    unittest.main()
