"""Device-centric data models for the Eero household monitor."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

AGGREGATE_DEVICE = "__total__"
API_DEVICE_ID = "__api__"


@dataclass(frozen=True)
class DeviceSnapshot:
    device_id: str
    name: str
    mac: str | None
    ip: str | None
    is_online: bool
    connection: str
    signal: float | None = None
    last_seen: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeviceRates:
    device_id: str
    name: str
    timestamp: float
    recv_bps: float
    sent_bps: float
    is_online: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AggregateDeviceRates:
    timestamp: float
    recv_bps: float
    sent_bps: float
    devices: tuple[DeviceRates, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["devices"] = [item.to_dict() for item in self.devices]
        return data


@dataclass(frozen=True)
class HealthEvent:
    timestamp: float
    device_id: str
    event_type: str
    severity: str
    message: str
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
