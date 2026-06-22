"""Data models for interface snapshots and transfer rates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class InterfaceStats:
    name: str
    is_up: bool
    speed_mbps: int
    duplex: str
    mtu: int
    bytes_recv: int
    bytes_sent: int
    packets_recv: int
    packets_sent: int
    errin: int
    errout: int
    dropin: int
    dropout: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InterfaceRates:
    name: str
    timestamp: float
    recv_bps: float
    sent_bps: float
    recv_pps: float
    sent_pps: float

    @property
    def total_bps(self) -> float:
        return self.recv_bps + self.sent_bps

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AggregateRates:
    timestamp: float
    recv_bps: float
    sent_bps: float
    interfaces: tuple[InterfaceRates, ...]

    @property
    def total_bps(self) -> float:
        return self.recv_bps + self.sent_bps

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["interfaces"] = [item.to_dict() for item in self.interfaces]
        return data
