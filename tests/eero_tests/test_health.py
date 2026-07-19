from __future__ import annotations

import unittest

from eero_monitor.health import HealthMonitor, api_error_event, auth_error_event
from eero_monitor.models import API_DEVICE_ID, DeviceSnapshot


class HealthTests(unittest.TestCase):
    def test_online_offline_transitions(self) -> None:
        monitor = HealthMonitor()
        first = [
            DeviceSnapshot("a", "A", None, None, True, "wifi"),
        ]
        self.assertEqual(monitor.evaluate(1.0, first), [])

        second = [
            DeviceSnapshot("a", "A", None, None, False, "wifi"),
        ]
        events = monitor.evaluate(2.0, second)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "offline")

        third = [
            DeviceSnapshot("a", "A", None, None, True, "wifi"),
        ]
        events = monitor.evaluate(3.0, third)
        self.assertEqual(events[0].event_type, "online")

    def test_api_and_auth_helpers(self) -> None:
        auth = auth_error_event(1.0, "bad token")
        self.assertEqual(auth.device_id, API_DEVICE_ID)
        self.assertEqual(auth.event_type, "auth_error")
        api = api_error_event(1.0, "timeout")
        self.assertEqual(api.event_type, "api_error")


if __name__ == "__main__":
    unittest.main()
