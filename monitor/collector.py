"""Collect network interface statistics and compute transfer rates."""

from __future__ import annotations

import fnmatch
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable, Iterator

import psutil

from monitor.formatting import duplex_label
from monitor.models import AggregateRates, InterfaceRates, InterfaceStats

DEFAULT_EXCLUDE_PATTERNS = ("lo", "lo*", "docker*", "veth*", "br-*", "virbr*")


def should_include_interface(
    name: str,
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> bool:
    """Return True when an interface should be included in monitoring."""
    include_patterns = tuple(include or ())
    exclude_patterns = tuple(exclude or DEFAULT_EXCLUDE_PATTERNS)

    if include_patterns:
        return any(fnmatch.fnmatch(name, pattern) for pattern in include_patterns)

    return not any(fnmatch.fnmatch(name, pattern) for pattern in exclude_patterns)


def list_interface_stats(
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> list[InterfaceStats]:
    """Return a one-shot snapshot for each monitored interface."""
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    interfaces: list[InterfaceStats] = []

    for name in sorted(counters):
        if not should_include_interface(name, include=include, exclude=exclude):
            continue

        nic = stats.get(name)
        if nic is None:
            continue

        io = counters[name]
        interfaces.append(
            InterfaceStats(
                name=name,
                is_up=nic.isup,
                speed_mbps=nic.speed,
                duplex=duplex_label(nic.duplex),
                mtu=nic.mtu,
                bytes_recv=io.bytes_recv,
                bytes_sent=io.bytes_sent,
                packets_recv=io.packets_recv,
                packets_sent=io.packets_sent,
                errin=io.errin,
                errout=io.errout,
                dropin=io.dropin,
                dropout=io.dropout,
            )
        )

    return interfaces


@dataclass
class _CounterSample:
    timestamp: float
    bytes_recv: int
    bytes_sent: int
    packets_recv: int
    packets_sent: int


def _read_counter_samples(
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> dict[str, _CounterSample]:
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    now = time.time()
    samples: dict[str, _CounterSample] = {}

    for name, io in counters.items():
        if not should_include_interface(name, include=include, exclude=exclude):
            continue
        if name not in stats:
            continue

        samples[name] = _CounterSample(
            timestamp=now,
            bytes_recv=io.bytes_recv,
            bytes_sent=io.bytes_sent,
            packets_recv=io.packets_recv,
            packets_sent=io.packets_sent,
        )

    return samples


def _compute_rates(
    previous: dict[str, _CounterSample],
    current: dict[str, _CounterSample],
) -> list[InterfaceRates]:
    rates: list[InterfaceRates] = []

    for name, current_sample in sorted(current.items()):
        previous_sample = previous.get(name)
        if previous_sample is None:
            continue

        elapsed = current_sample.timestamp - previous_sample.timestamp
        if elapsed <= 0:
            continue

        recv_delta = current_sample.bytes_recv - previous_sample.bytes_recv
        sent_delta = current_sample.bytes_sent - previous_sample.bytes_sent
        recv_packets_delta = current_sample.packets_recv - previous_sample.packets_recv
        sent_packets_delta = current_sample.packets_sent - previous_sample.packets_sent

        if recv_delta < 0 or sent_delta < 0:
            continue

        rates.append(
            InterfaceRates(
                name=name,
                timestamp=current_sample.timestamp,
                recv_bps=(recv_delta * 8) / elapsed,
                sent_bps=(sent_delta * 8) / elapsed,
                recv_pps=recv_packets_delta / elapsed,
                sent_pps=sent_packets_delta / elapsed,
            )
        )

    return rates


def sample_rates(
    previous: dict[str, _CounterSample] | None,
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> tuple[dict[str, _CounterSample], AggregateRates | None]:
    """Read counters and compute per-interface rates from the previous sample."""
    current = _read_counter_samples(include=include, exclude=exclude)
    if not previous:
        return current, None

    interface_rates = _compute_rates(previous, current)
    if not interface_rates:
        return current, None

    recv_bps = sum(item.recv_bps for item in interface_rates)
    sent_bps = sum(item.sent_bps for item in interface_rates)
    return current, AggregateRates(
        timestamp=interface_rates[0].timestamp,
        recv_bps=recv_bps,
        sent_bps=sent_bps,
        interfaces=tuple(interface_rates),
    )


class MetricsStore:
    """In-memory ring buffer for recent aggregate and per-interface samples."""

    def __init__(self, *, max_samples: int = 3600) -> None:
        self.max_samples = max_samples
        self._samples: Deque[AggregateRates] = deque(maxlen=max_samples)

    def add(self, sample: AggregateRates) -> None:
        self._samples.append(sample)

    def __len__(self) -> int:
        return len(self._samples)

    def latest(self) -> AggregateRates | None:
        if not self._samples:
            return None
        return self._samples[-1]

    def history(self) -> list[AggregateRates]:
        return list(self._samples)

    def interface_history(self, name: str) -> list[InterfaceRates]:
        history: list[InterfaceRates] = []
        for sample in self._samples:
            for interface in sample.interfaces:
                if interface.name == name:
                    history.append(interface)
                    break
        return history


class BandwidthCollector:
    """Background sampler that stores recent transfer-rate history in memory."""

    def __init__(
        self,
        *,
        interval: float = 1.0,
        history_size: int = 3600,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
    ) -> None:
        self.interval = interval
        self.include = tuple(include or ())
        self.exclude = tuple(exclude or ())
        self.store = MetricsStore(max_samples=history_size)
        self._previous: dict[str, _CounterSample] | None = None

    def prime(self) -> None:
        """Take an initial counter reading so the next sample can compute rates."""
        self._previous, _ = sample_rates(
            None,
            include=self.include or None,
            exclude=self.exclude or None,
        )

    def sample_once(self) -> AggregateRates | None:
        """Read counters once and append computed rates to the in-memory store."""
        self._previous, aggregate = sample_rates(
            self._previous,
            include=self.include or None,
            exclude=self.exclude or None,
        )
        if aggregate is not None:
            self.store.add(aggregate)
        return aggregate

    def watch(self) -> Iterator[AggregateRates]:
        """Yield transfer-rate samples forever at the configured interval."""
        self.prime()
        time.sleep(self.interval)

        while True:
            aggregate = self.sample_once()
            if aggregate is not None:
                yield aggregate
            time.sleep(self.interval)
