from __future__ import annotations

import unittest
from typing import Any

from eero_monitor.client import EeroClient
from eero_monitor.models import DeviceSnapshot


class FakeTransport:
    def __init__(self, devices: list[dict[str, Any]]) -> None:
        self.devices = devices

    def fetch_devices(self, network_id: str) -> list[dict[str, Any]]:
        assert network_id == "net1"
        return self.devices


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
                    "wireless_bitrate_down": 1_000_000,
                    "wireless_bitrate_up": 200_000,
                },
                {
                    "url": "/2.2/devices/bbb",
                    "hostname": "tv",
                    "mac": "11:22:33:44:55:66",
                    "connected": False,
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


if __name__ == "__main__":
    unittest.main()
