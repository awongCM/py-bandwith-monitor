"""Background sampling service for the Eero household dashboard."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from eero_monitor.collector import DeviceCollector
from eero_monitor.health import HealthMonitor, api_error_event, auth_error_event
from eero_monitor.models import DeviceSnapshot
from eero_monitor.storage import MetricsDatabase

logger = logging.getLogger(__name__)


class SampleClient(Protocol):
    def list_device_samples(self) -> list[tuple[DeviceSnapshot, float, float]]: ...


class SamplingService:
    """Run the device collector in a background thread and persist samples."""

    def __init__(
        self,
        database: MetricsDatabase,
        *,
        client: SampleClient,
        interval: float = 5.0,
        retention_days: int = 7,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        maintenance_interval_samples: int = 60,
    ) -> None:
        self.database = database
        self.client = client
        self.interval = interval
        self.retention_days = retention_days
        self.on_sample = on_sample
        self.maintenance_interval_samples = maintenance_interval_samples
        self.collector = DeviceCollector(client, interval=interval)
        self.health_monitor = HealthMonitor()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples_since_maintenance = 0
        self._last_sample_failed = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="eero-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 2)
            self._thread = None

    def sample_once(self) -> None:
        try:
            aggregate = self.collector.sample()
            snapshots = list(self.collector.last_snapshots)
        except Exception as exc:  # noqa: BLE001 - surface as health, keep sampler alive
            timestamp = time.time()
            message = str(exc) or exc.__class__.__name__
            if "401" in message.lower() or "auth" in message.lower():
                event = auth_error_event(timestamp, message)
            else:
                event = api_error_event(timestamp, message)
            try:
                self.database.insert_health_event(event)
            except Exception:  # noqa: BLE001
                logger.exception("failed to persist health event")
            if self.on_sample is not None:
                self.on_sample(
                    {
                        "type": "sample",
                        "timestamp": timestamp,
                        "recv_bps": 0.0,
                        "sent_bps": 0.0,
                        "devices": [],
                        "snapshots": [],
                        "health": [event.to_dict()],
                        "error": message,
                    }
                )
            self._last_sample_failed = True
            return

        if self._last_sample_failed:
            self.database.clear_api_health_events()
            self._last_sample_failed = False

        self.database.insert_rates(aggregate)
        if snapshots:
            self.database.insert_device_snapshots(aggregate.timestamp, snapshots)

        events = self.health_monitor.evaluate(aggregate.timestamp, snapshots)
        for event in events:
            self.database.insert_health_event(event)

        self._samples_since_maintenance += 1
        if self._samples_since_maintenance >= self.maintenance_interval_samples:
            self._samples_since_maintenance = 0
            threading.Thread(
                target=self.database.purge_older_than,
                kwargs={"days": float(self.retention_days)},
                name="eero-retention",
                daemon=True,
            ).start()

        if self.on_sample is not None:
            self.on_sample(
                {
                    "type": "sample",
                    "timestamp": aggregate.timestamp,
                    "recv_bps": aggregate.recv_bps,
                    "sent_bps": aggregate.sent_bps,
                    "devices": [item.to_dict() for item in aggregate.devices],
                    "snapshots": [item.to_dict() for item in snapshots],
                    "health": [event.to_dict() for event in events],
                }
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.sample_once()
            self._stop_event.wait(self.interval)


class WebSocketBridge:
    """Bridge sync sampling callbacks to async WebSocket broadcasts."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broadcast: Callable[[dict[str, Any]], Any] | None = None

    def bind(
        self,
        loop: asyncio.AbstractEventLoop,
        broadcast: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._loop = loop
        self._broadcast = broadcast

    def publish(self, payload: dict[str, Any]) -> None:
        if self._loop is None or self._broadcast is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._broadcast(payload))
        )
