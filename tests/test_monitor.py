from __future__ import annotations

import unittest
from unittest.mock import patch

import psutil

from monitor.collector import (
    BandwidthCollector,
    should_include_interface,
    sample_rates,
)
from monitor.formatting import bytes2human, rate2human


class InterfaceFilterTests(unittest.TestCase):
    def test_default_excludes_loopback_and_docker(self) -> None:
        self.assertFalse(should_include_interface("lo"))
        self.assertFalse(should_include_interface("docker0"))
        self.assertFalse(should_include_interface("veth123"))
        self.assertTrue(should_include_interface("eth0"))

    def test_include_patterns_override_default(self) -> None:
        self.assertTrue(should_include_interface("lo", include=("lo",)))


class FormattingTests(unittest.TestCase):
    def test_bytes2human(self) -> None:
        self.assertEqual(bytes2human(0), "0 B")
        self.assertEqual(bytes2human(1024), "1.0 KiB")

    def test_rate2human(self) -> None:
        self.assertEqual(rate2human(500), "500.00 bit/s")
        self.assertEqual(rate2human(2_500_000), "2.50 Mbit/s")


class CollectorTests(unittest.TestCase):
    def test_sample_rates_computes_per_interface_rates(self) -> None:
        previous = {
            "eth0": type(
                "Sample",
                (),
                {
                    "timestamp": 0.0,
                    "bytes_recv": 1_000,
                    "bytes_sent": 2_000,
                    "packets_recv": 10,
                    "packets_sent": 20,
                },
            )()
        }
        current = {
            "eth0": type(
                "Sample",
                (),
                {
                    "timestamp": 1.0,
                    "bytes_recv": 1_250,
                    "bytes_sent": 2_500,
                    "packets_recv": 15,
                    "packets_sent": 25,
                },
            )()
        }

        from monitor.collector import _compute_rates

        rates = _compute_rates(previous, current)
        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0].name, "eth0")
        self.assertEqual(rates[0].recv_bps, 2000.0)
        self.assertEqual(rates[0].sent_bps, 4000.0)

    @patch("monitor.collector.psutil.net_io_counters")
    @patch("monitor.collector.psutil.net_if_stats")
    def test_list_and_watch_flow(self, mock_stats, mock_counters) -> None:
        mock_stats.return_value = {
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
        mock_counters.return_value = {
            "eth0": type(
                "Counters",
                (),
                {
                    "bytes_recv": 100,
                    "bytes_sent": 200,
                    "packets_recv": 1,
                    "packets_sent": 2,
                    "errin": 0,
                    "errout": 0,
                    "dropin": 0,
                    "dropout": 0,
                },
            )()
        }

        collector = BandwidthCollector(interval=0.01, history_size=5, include=("eth0",))
        collector.prime()
        sample = collector.sample_once()
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(len(collector.store), 1)
        self.assertEqual(sample.interfaces[0].name, "eth0")


if __name__ == "__main__":
    unittest.main()
