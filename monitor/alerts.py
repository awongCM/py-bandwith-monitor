"""Threshold alert evaluation with cooldown deduplication."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from monitor.alerts_settings import AlertSettings
from monitor.models import (
    AGGREGATE_INTERFACE,
    AggregateRates,
    AlertEvent,
    HealthEvent,
)


# Accept positional or keyword `minutes` so storage's keyword-only API works.
HistoryGetter = Callable[..., list[dict[str, Any]]]
Clock = Callable[[], float]

HEALTH_ALERT_TYPES = frozenset({"high_errors", "high_drops", "link_down"})


class AlertEngine:
    """Evaluate bandwidth and health-derived alert rules."""

    def __init__(
        self,
        settings: AlertSettings,
        *,
        interval: float = 1.0,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self.interval = interval
        self._clock = clock or time.time
        self._cooldowns: dict[str, float] = {}
        self._bandwidth_streak: dict[str, int] = {}
        self._history_bootstrapped = False

    def evaluate(
        self,
        sample: AggregateRates,
        _interfaces: list[object],
        health_events: list[HealthEvent],
        *,
        history_getter: HistoryGetter | None = None,
    ) -> list[AlertEvent]:
        alerts: list[AlertEvent] = []

        if self.settings.bandwidth_enabled:
            if not self._history_bootstrapped and history_getter is not None:
                self._bootstrap_rate_streaks(sample, history_getter)
                self._history_bootstrapped = True
            alerts.extend(self._evaluate_rate_rules(sample))

        if self.settings.notify_health_events:
            alerts.extend(self._evaluate_health_alerts(health_events))

        return alerts

    def _required_streak(self) -> int:
        return max(
            1,
            math.ceil(self.settings.bandwidth_sustained_seconds / self.interval),
        )

    def _rate_rules(self) -> list[tuple[str, str, float]]:
        """Return (rule_key, alert_type, threshold_bps) for configured rate rules."""
        rules: list[tuple[str, str, float]] = []
        if self.settings.bandwidth_mbps_threshold is not None:
            rules.append(
                (
                    f"bandwidth:{AGGREGATE_INTERFACE}",
                    "bandwidth_high",
                    self.settings.bandwidth_mbps_threshold * 1_000_000,
                )
            )
        if self.settings.recv_bps_threshold is not None:
            rules.append(
                (
                    f"recv:{AGGREGATE_INTERFACE}",
                    "recv_high",
                    self.settings.recv_bps_threshold,
                )
            )
        if self.settings.sent_bps_threshold is not None:
            rules.append(
                (
                    f"sent:{AGGREGATE_INTERFACE}",
                    "sent_high",
                    self.settings.sent_bps_threshold,
                )
            )
        return rules

    def _sample_value(self, sample: AggregateRates, alert_type: str) -> float:
        if alert_type == "recv_high":
            return sample.recv_bps
        if alert_type == "sent_high":
            return sample.sent_bps
        return sample.total_bps

    def _history_value(self, row: dict[str, Any], alert_type: str) -> float:
        recv = float(row["recv_bps"])
        sent = float(row["sent_bps"])
        if alert_type == "recv_high":
            return recv
        if alert_type == "sent_high":
            return sent
        return recv + sent

    def _bootstrap_rate_streaks(
        self,
        _sample: AggregateRates,
        history_getter: HistoryGetter,
    ) -> None:
        # Seed streaks from history only; the current sample is applied next.
        minutes = self.settings.bandwidth_sustained_seconds / 60.0
        history = history_getter(AGGREGATE_INTERFACE, minutes=minutes)

        for rule_key, alert_type, threshold_bps in self._rate_rules():
            streak = 0
            for row in history:
                if self._history_value(row, alert_type) >= threshold_bps:
                    streak += 1
                else:
                    streak = 0
            self._bandwidth_streak[rule_key] = streak

    def _evaluate_rate_rules(self, sample: AggregateRates) -> list[AlertEvent]:
        alerts: list[AlertEvent] = []
        required = self._required_streak()

        for rule_key, alert_type, threshold_bps in self._rate_rules():
            value = self._sample_value(sample, alert_type)
            if value >= threshold_bps:
                self._bandwidth_streak[rule_key] = (
                    self._bandwidth_streak.get(rule_key, 0) + 1
                )
            else:
                self._bandwidth_streak[rule_key] = 0
                continue

            if self._bandwidth_streak[rule_key] < required:
                continue
            if self._in_cooldown(rule_key):
                continue

            self._mark_fired(rule_key)
            mbps = value / 1_000_000
            threshold_mbps = threshold_bps / 1_000_000
            label = {
                "bandwidth_high": "Aggregate bandwidth",
                "recv_high": "Download rate",
                "sent_high": "Upload rate",
            }[alert_type]
            alerts.append(
                AlertEvent(
                    timestamp=sample.timestamp,
                    rule_id=rule_key,
                    alert_type=alert_type,
                    severity="warning",
                    interface=AGGREGATE_INTERFACE,
                    message=(
                        f"{label} {mbps:.2f} Mbps exceeded "
                        f"{threshold_mbps:.2f} Mbps for "
                        f"{self.settings.bandwidth_sustained_seconds:.0f}s"
                    ),
                    value=value,
                    threshold=threshold_bps,
                )
            )
        return alerts

    def _evaluate_health_alerts(
        self,
        health_events: list[HealthEvent],
    ) -> list[AlertEvent]:
        alerts: list[AlertEvent] = []
        for event in health_events:
            if event.event_type not in HEALTH_ALERT_TYPES:
                continue

            rule_key = f"health:{event.event_type}:{event.interface}"
            if self._in_cooldown(rule_key):
                continue

            self._mark_fired(rule_key)
            alerts.append(
                AlertEvent(
                    timestamp=event.timestamp,
                    rule_id=rule_key,
                    alert_type=event.event_type,
                    severity=event.severity,
                    interface=event.interface,
                    message=event.message,
                    value=event.value,
                )
            )
        return alerts

    def _in_cooldown(self, rule_key: str) -> bool:
        last_fired = self._cooldowns.get(rule_key)
        if last_fired is None:
            return False
        return (self._clock() - last_fired) < self.settings.cooldown_seconds

    def _mark_fired(self, rule_key: str) -> None:
        self._cooldowns[rule_key] = self._clock()
