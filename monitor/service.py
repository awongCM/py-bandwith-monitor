"""Background sampling service for the dashboard."""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Iterable

from monitor.alerts import AlertEngine
from monitor.collector import BandwidthCollector, list_interface_stats
from monitor.health import HealthMonitor
from monitor.models import LOCAL_HOST_ID, AggregateRates, AlertEvent, InterfaceStats
from monitor.notifiers import Notifier
from monitor.retention import RetentionSettings
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
        retention: RetentionSettings | None = None,
        retention_days: int | None = None,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        alert_engine: AlertEngine | None = None,
        notifiers: Iterable[Notifier] | None = None,
        error_delta_threshold: int | None = None,
        host_id: str = LOCAL_HOST_ID,
    ) -> None:
        self.database = database
        self.host_id = host_id
        self.interval = interval
        self.include = tuple(include or ())
        self.exclude = tuple(exclude or ())
        if retention is not None and retention_days is not None:
            retention = RetentionSettings(
                raw_retention_days=retention_days,
                minute_retention_days=retention.minute_retention_days,
                hourly_retention_days=retention.hourly_retention_days,
                daily_retention_days=retention.daily_retention_days,
                maintenance_interval_samples=retention.maintenance_interval_samples,
            )
        elif retention is None:
            retention = RetentionSettings(
                raw_retention_days=retention_days if retention_days is not None else 7,
            )
        self.retention = retention
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
        self._health_monitors: dict[str, HealthMonitor] = {
            host_id: self.health_monitor
        }
        self._alert_engines: dict[str, AlertEngine | None] = {
            host_id: self.alert_engine
        }
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples_since_maintenance = 0

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

    def _rate_history(self, interface: str, *, minutes: float) -> list[dict[str, Any]]:
        return self.database.get_rate_history(
            interface,
            minutes=minutes,
            host_id=self.host_id,
        )

    def _handle_sample(self, sample: AggregateRates) -> None:
        interfaces = list_interface_stats(
            include=self.include or None,
            exclude=self.exclude or None,
        )
        self._persist_sample(self.host_id, sample, interfaces)

    def ingest_remote(
        self,
        host_id: str,
        sample: AggregateRates,
        snapshots: list[InterfaceStats],
    ) -> None:
        """Persist a sample posted by a remote agent."""
        self._persist_sample(host_id, sample, snapshots)

    def _persist_sample(
        self,
        host_id: str,
        sample: AggregateRates,
        snapshots: list[InterfaceStats],
    ) -> None:
        self.database.insert_rates(sample, host_id=host_id)
        if snapshots:
            self.database.insert_interface_snapshots(
                sample.timestamp,
                snapshots,
                host_id=host_id,
            )

        monitor = self._health_for_host(host_id)
        events = [
            replace(event, host_id=host_id)
            for event in monitor.evaluate(sample.timestamp, snapshots)
        ]
        for event in events:
            self.database.insert_health_event(event)

        alerts: list[AlertEvent] = []
        alert_engine = self._alert_for_host(host_id)
        if alert_engine is not None:
            alerts = alert_engine.evaluate(
                sample,
                snapshots,
                events,
                history_getter=lambda interface, *, minutes: self.database.get_rate_history(
                    interface,
                    minutes=minutes,
                    host_id=host_id,
                ),
            )
            alerts = [replace(alert, host_id=host_id) for alert in alerts]
            for alert in alerts:
                self.database.insert_alert_event(alert)
                self._dispatch_alert(alert)

        self._samples_since_maintenance += 1
        if self._samples_since_maintenance >= self.retention.maintenance_interval_samples:
            self._samples_since_maintenance = 0
            # Run off the sampler thread; MetricsDatabase serializes via its lock.
            threading.Thread(
                target=self.database.run_retention_maintenance,
                args=(self.retention,),
                name="retention-maintenance",
                daemon=True,
            ).start()

        if self.on_sample is not None:
            payload = {
                "type": "sample",
                "host_id": host_id,
                "timestamp": sample.timestamp,
                "recv_bps": sample.recv_bps,
                "sent_bps": sample.sent_bps,
                "total_bps": sample.total_bps,
                "interfaces": [item.to_dict() for item in sample.interfaces],
                "snapshots": [item.to_dict() for item in snapshots],
                "health": [event.to_dict() for event in events],
                "alerts": [alert.to_dict() for alert in alerts],
            }
            self.on_sample(payload)

    def _health_for_host(self, host_id: str) -> HealthMonitor:
        return self._health_monitors.setdefault(host_id, HealthMonitor(
            error_delta_threshold=self.health_monitor.error_delta_threshold,
            drop_delta_threshold=self.health_monitor.drop_delta_threshold,
        ))

    def _alert_for_host(self, host_id: str) -> AlertEngine | None:
        if host_id not in self._alert_engines:
            self._alert_engines[host_id] = (
                copy.deepcopy(self.alert_engine)
                if self.alert_engine is not None
                else None
            )
        return self._alert_engines[host_id]

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
