"""Detect device online/offline transitions and API/auth failures."""

from __future__ import annotations

from eero_monitor.models import API_DEVICE_ID, DeviceSnapshot, HealthEvent


class HealthMonitor:
    """Track household device presence transitions."""

    def __init__(self) -> None:
        self._online: dict[str, bool] = {}

    def evaluate(
        self,
        timestamp: float,
        devices: list[DeviceSnapshot],
    ) -> list[HealthEvent]:
        events: list[HealthEvent] = []
        for device in devices:
            previous = self._online.get(device.device_id)
            if previous is None:
                self._online[device.device_id] = device.is_online
                continue
            if previous and not device.is_online:
                events.append(
                    HealthEvent(
                        timestamp=timestamp,
                        device_id=device.device_id,
                        event_type="offline",
                        severity="warning",
                        message=f"{device.name} went offline",
                    )
                )
            elif not previous and device.is_online:
                events.append(
                    HealthEvent(
                        timestamp=timestamp,
                        device_id=device.device_id,
                        event_type="online",
                        severity="info",
                        message=f"{device.name} came online",
                    )
                )
            self._online[device.device_id] = device.is_online
        return events


def auth_error_event(timestamp: float, message: str) -> HealthEvent:
    return HealthEvent(
        timestamp=timestamp,
        device_id=API_DEVICE_ID,
        event_type="auth_error",
        severity="critical",
        message=message,
    )


def api_error_event(timestamp: float, message: str) -> HealthEvent:
    return HealthEvent(
        timestamp=timestamp,
        device_id=API_DEVICE_ID,
        event_type="api_error",
        severity="warning",
        message=message,
    )
