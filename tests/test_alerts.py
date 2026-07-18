from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from types import SimpleNamespace

from monitor.alerts import AlertEngine
from monitor.alerts_settings import AlertSettings
from monitor.models import (
    AGGREGATE_INTERFACE,
    AggregateRates,
    AlertEvent,
    HealthEvent,
    InterfaceRates,
)
from monitor.notifiers import WebhookNotifier


class AlertEngineTests(unittest.TestCase):
    def _sample(self, total_mbps: float, timestamp: float = 100.0) -> AggregateRates:
        bps = total_mbps * 1_000_000
        return AggregateRates(
            timestamp=timestamp,
            recv_bps=bps / 2,
            sent_bps=bps / 2,
            interfaces=(
                InterfaceRates(
                    name="eth0",
                    timestamp=timestamp,
                    recv_bps=bps / 2,
                    sent_bps=bps / 2,
                    recv_pps=0.0,
                    sent_pps=0.0,
                ),
            ),
        )

    def test_bandwidth_alert_requires_sustained_samples(self) -> None:
        clock = MagicMock(side_effect=[100.0, 100.0, 100.0, 100.0])
        settings = AlertSettings(
            bandwidth_mbps_threshold=100.0,
            bandwidth_sustained_seconds=3.0,
            cooldown_seconds=60.0,
        )
        engine = AlertEngine(settings, interval=1.0, clock=clock)

        first = engine.evaluate(self._sample(150.0, timestamp=1.0), [], [])
        second = engine.evaluate(self._sample(150.0, timestamp=2.0), [], [])
        self.assertEqual(first, [])
        self.assertEqual(second, [])

        third = engine.evaluate(self._sample(150.0, timestamp=3.0), [], [])
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0].alert_type, "bandwidth_high")

    def test_bandwidth_alert_respects_cooldown(self) -> None:
        clock = MagicMock(side_effect=[100.0, 100.0, 100.0, 150.0, 150.0])
        settings = AlertSettings(
            bandwidth_mbps_threshold=100.0,
            bandwidth_sustained_seconds=1.0,
            cooldown_seconds=60.0,
        )
        engine = AlertEngine(settings, interval=1.0, clock=clock)

        first = engine.evaluate(self._sample(150.0, timestamp=1.0), [], [])
        self.assertEqual(len(first), 1)

        second = engine.evaluate(self._sample(150.0, timestamp=2.0), [], [])
        self.assertEqual(second, [])

    def test_health_events_bridge_to_alerts_with_cooldown(self) -> None:
        clock = MagicMock(side_effect=[200.0, 200.0, 260.0])
        settings = AlertSettings(cooldown_seconds=60.0)
        engine = AlertEngine(settings, interval=1.0, clock=clock)
        sample = self._sample(1.0, timestamp=1.0)
        health = [
            HealthEvent(
                timestamp=1.0,
                interface="eth0",
                event_type="high_errors",
                severity="warning",
                message="eth0 reported 12 new errors since last sample",
                value=12.0,
            )
        ]

        first = engine.evaluate(sample, [], health)
        second = engine.evaluate(sample, [], health)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].alert_type, "high_errors")
        self.assertEqual(second, [])

    def test_history_bootstrap_counts_recent_high_bandwidth(self) -> None:
        settings = AlertSettings(
            bandwidth_mbps_threshold=100.0,
            bandwidth_sustained_seconds=3.0,
        )
        engine = AlertEngine(settings, interval=1.0, clock=lambda: 100.0)

        def history_getter(_interface: str, _minutes: float) -> list[dict[str, float]]:
            return [
                {"recv_bps": 60_000_000, "sent_bps": 60_000_000},
                {"recv_bps": 60_000_000, "sent_bps": 60_000_000},
            ]

        alerts = engine.evaluate(
            self._sample(150.0, timestamp=3.0),
            [],
            [],
            history_getter=history_getter,
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].interface, AGGREGATE_INTERFACE)


class AlertSettingsResolveTests(unittest.TestCase):
    def test_resolve_reads_app_config_thresholds(self) -> None:
        app_config = SimpleNamespace(
            thresholds=SimpleNamespace(
                total_bps=100_000_000.0,
                recv_bps=50_000_000.0,
                sent_bps=None,
                sustained_errors=25,
            )
        )
        with patch.dict("os.environ", {}, clear=True):
            settings = AlertSettings.resolve(app_config=app_config)

        self.assertEqual(settings.bandwidth_mbps_threshold, 100.0)
        self.assertEqual(settings.recv_bps_threshold, 50_000_000.0)
        self.assertIsNone(settings.sent_bps_threshold)
        self.assertEqual(settings.error_delta_threshold, 25)

    def test_env_overrides_app_config(self) -> None:
        app_config = SimpleNamespace(
            thresholds=SimpleNamespace(
                total_bps=100_000_000.0,
                recv_bps=None,
                sent_bps=None,
                sustained_errors=25,
            )
        )
        env = {
            "ALERT_BANDWIDTH_MBPS": "200",
            "ALERT_ERROR_DELTA": "5",
            "ALERT_WEBHOOK_URL": "https://hooks.example/test",
        }
        with patch.dict("os.environ", env, clear=True):
            settings = AlertSettings.resolve(app_config=app_config)

        self.assertEqual(settings.bandwidth_mbps_threshold, 200.0)
        self.assertEqual(settings.error_delta_threshold, 5)
        self.assertEqual(settings.webhook_url, "https://hooks.example/test")


class WebhookNotifierTests(unittest.TestCase):
    def test_webhook_posts_json_payload(self) -> None:
        client = MagicMock()
        notifier = WebhookNotifier("https://example.test/hook", client=client)
        alert = AlertEvent(
            timestamp=1.0,
            rule_id="bandwidth:__total__",
            alert_type="bandwidth_high",
            severity="warning",
            interface=AGGREGATE_INTERFACE,
            message="test alert",
            value=150_000_000.0,
            threshold=100_000_000.0,
        )

        notifier.notify(alert)

        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "https://example.test/hook")
        self.assertEqual(kwargs["json"]["text"], "test alert")
        self.assertEqual(kwargs["json"]["alert"]["alert_type"], "bandwidth_high")

    @patch("monitor.notifiers.httpx.Client")
    def test_webhook_swallows_http_errors(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.side_effect = RuntimeError("network down")
        mock_client_cls.return_value = client

        notifier = WebhookNotifier("https://example.test/hook")
        alert = AlertEvent(
            timestamp=1.0,
            rule_id="health:high_errors:eth0",
            alert_type="high_errors",
            severity="warning",
            interface="eth0",
            message="errors",
        )

        notifier.notify(alert)
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
