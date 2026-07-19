from __future__ import annotations

import unittest

from eero_monitor.collector import DeviceCollector
from eero_monitor.models import DeviceSnapshot


class FakeClient:
    def __init__(self, samples):
        self._samples = samples

    def list_device_samples(self):
        return self._samples


class CollectorTests(unittest.TestCase):
    def test_aggregates_online_and_offline(self) -> None:
        samples = [
            (
                DeviceSnapshot(
                    device_id="a",
                    name="A",
                    mac=None,
                    ip=None,
                    is_online=True,
                    connection="wifi",
                ),
                100.0,
                50.0,
            ),
            (
                DeviceSnapshot(
                    device_id="b",
                    name="B",
                    mac=None,
                    ip=None,
                    is_online=False,
                    connection="unknown",
                ),
                0.0,
                0.0,
            ),
        ]
        collector = DeviceCollector(FakeClient(samples), interval=5.0)
        aggregate = collector.sample()
        self.assertEqual(aggregate.recv_bps, 100.0)
        self.assertEqual(aggregate.sent_bps, 50.0)
        self.assertEqual(len(aggregate.devices), 2)

    def test_empty_device_list(self) -> None:
        collector = DeviceCollector(FakeClient([]), interval=5.0)
        aggregate = collector.sample()
        self.assertEqual(aggregate.recv_bps, 0.0)
        self.assertEqual(aggregate.devices, ())


if __name__ == "__main__":
    unittest.main()
