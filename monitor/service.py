"""Background sampling service for the dashboard."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, Iterable

from monitor.alerts import AlertEngine
from monitor.collector import BandwidthCollector, list_interface_stats
from monitor.health import HealthMonitor
from monitor.models import AggregateRates, AlertEvent
from monitor.notifiers import Notifier
from monitor.storage import MetricsDatabase


class SamplingService:
    """Run the collector in a background thread and persist each sample."""

    def __init__(
        self,
        database: MetricsDatabase,
        *,
        interval: float = 1.0,
        history_size: int = 3600,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        retention_days: int = 7,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        alert_engine: AlertEngine | None = None,
        notifiers: Iterable[Notifier] | None = None,
        error_delta_threshold: int | None = None,
    ) -> None:
        self.database = database
        self.interval = interval
        self.include = tuple(include or ())
        self.exclude = tuple(exclude or ())
        self.retention_days = retention_days
        self.on_sample = on_sample
        self.alert_engine = alert_engine
        self.notifiers = tuple(notifiers or ())
        self.collector = BandwidthCollector(
            interval=interval,
            history_size=history_size,
            include=include,
            exclude=exclude,
        )
        health_kwargs: dict[str, int] = {}
        if error_delta_threshold is not None:
            health_kwargs["error_delta_threshold"] = error_delta_threshold
        self.health_monitor = HealthMonitor(**health_kwargs)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples_since_prune = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="bandwidth-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 2)
            self._thread = None

    def _run(self) -> None:
        for sample in self.collector.watch(stop_check=self._stop_event.is_set):
            if self._stop_event.is_set():
                break
            self._handle_sample(sample)

    def _handle_sample(self, sample: AggregateRates) -> None:
        interfaces = list_interface_stats(
            include=self.include or None,
            exclude=self.exclude or None,
        )
        self.database.insert_rates(sample)
        self.database.insert_interface_snapshots(sample.timestamp, interfaces)

        events = self.health_monitor.evaluate(sample.timestamp, interfaces)
        for event in events:
            self.database.insert_health_event(event)

        alerts: list[AlertEvent] = []
        if self.alert_engine is not None:
            alerts = self.alert_engine.evaluate(
                sample,
                interfaces,
                events,
                history_getter=self.database.get_rate_history,
            )
            for alert in alerts:
                self.database.insert_alert_event(alert)
                self._dispatch_alert(alert)

        self._samples_since_prune += 1
        if self._samples_since_prune >= 300:
            self.database.prune_old_data(days=self.retention_days)
            self._samples_since_prune = 0

        if self.on_sample is not None:
            payload = {
                "type": "sample",
                "timestamp": sample.timestamp,
                "recv_bps": sample.recv_bps,
                "sent_bps": sample.sent_bps,
                "total_bps": sample.total_bps,
                "interfaces": [item.to_dict() for item in sample.interfaces],
                "snapshots": [item.to_dict() for item in interfaces],
                "health": [event.to_dict() for event in events],
                "alerts": [alert.to_dict() for alert in alerts],
            }
            self.on_sample(payload)

    def _dispatch_alert(self, alert: AlertEvent) -> None:
        for notifier in self.notifiers:
            threading.Thread(
                target=notifier.notify,
                args=(alert,),
                name="alert-notifier",
                daemon=True,
            ).start()


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
