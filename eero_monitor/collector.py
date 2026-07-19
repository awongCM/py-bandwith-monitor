"""Poll the Eero client and build aggregate device rates."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Protocol

from eero_monitor.models import AggregateDeviceRates, DeviceRates, DeviceSnapshot


class SampleClient(Protocol):
    def list_device_samples(self) -> list[tuple[DeviceSnapshot, float, float]]: ...


class DeviceCollector:
    """Sample household device rates from an :class:`EeroClient`-like object."""

    def __init__(self, client: SampleClient, *, interval: float = 5.0) -> None:
        self.client = client
        self.interval = interval
        self.last_snapshots: tuple[DeviceSnapshot, ...] = ()

    def sample(self) -> AggregateDeviceRates:
        timestamp = time.time()
        device_rates: list[DeviceRates] = []
        snapshots: list[DeviceSnapshot] = []
        for snapshot, recv_bps, sent_bps in self.client.list_device_samples():
            snapshots.append(snapshot)
            device_rates.append(
                DeviceRates(
                    device_id=snapshot.device_id,
                    name=snapshot.name,
                    timestamp=timestamp,
                    recv_bps=float(recv_bps),
                    sent_bps=float(sent_bps),
                    is_online=snapshot.is_online,
                )
            )
        self.last_snapshots = tuple(snapshots)
        return AggregateDeviceRates(
            timestamp=timestamp,
            recv_bps=sum(item.recv_bps for item in device_rates),
            sent_bps=sum(item.sent_bps for item in device_rates),
            devices=tuple(device_rates),
        )

    def _wait(self, stop_check: Callable[[], bool] | None = None) -> None:
        deadline = time.monotonic() + self.interval
        while True:
            if stop_check and stop_check():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def watch(
        self,
        *,
        max_samples: int | None = None,
        duration: float | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> Iterator[AggregateDeviceRates]:
        started = time.monotonic()
        emitted = 0

        while True:
            if stop_check and stop_check():
                return
            if duration is not None and time.monotonic() - started >= duration:
                return

            yield self.sample()
            emitted += 1
            if max_samples is not None and emitted >= max_samples:
                return

            self._wait(stop_check)
            if stop_check and stop_check():
                return
