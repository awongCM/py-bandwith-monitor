"""Detect link and error-rate health events from interface snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from monitor.models import HealthEvent, InterfaceStats

ERROR_DELTA_THRESHOLD = 10
DROP_DELTA_THRESHOLD = 10


@dataclass
class _InterfaceHealthState:
    is_up: bool | None = None
    errin: int = 0
    errout: int = 0
    dropin: int = 0
    dropout: int = 0


class HealthMonitor:
    """Track interface state transitions and rising error/drop counters."""

    def __init__(
        self,
        *,
        error_delta_threshold: int = ERROR_DELTA_THRESHOLD,
        drop_delta_threshold: int = DROP_DELTA_THRESHOLD,
    ) -> None:
        self.error_delta_threshold = error_delta_threshold
        self.drop_delta_threshold = drop_delta_threshold
        self._state: dict[str, _InterfaceHealthState] = {}

    def evaluate(
        self,
        timestamp: float,
        interfaces: list[InterfaceStats],
    ) -> list[HealthEvent]:
        events: list[HealthEvent] = []

        for interface in interfaces:
            state = self._state.setdefault(interface.name, _InterfaceHealthState())
            events.extend(self._link_events(timestamp, interface, state))
            events.extend(self._counter_events(timestamp, interface, state))
            state.is_up = interface.is_up
            state.errin = interface.errin
            state.errout = interface.errout
            state.dropin = interface.dropin
            state.dropout = interface.dropout

        return events

    def _link_events(
        self,
        timestamp: float,
        interface: InterfaceStats,
        state: _InterfaceHealthState,
    ) -> list[HealthEvent]:
        if state.is_up is None:
            return []

        if state.is_up and not interface.is_up:
            return [
                HealthEvent(
                    timestamp=timestamp,
                    interface=interface.name,
                    event_type="link_down",
                    severity="critical",
                    message=f"{interface.name} link went down",
                )
            ]

        if not state.is_up and interface.is_up:
            return [
                HealthEvent(
                    timestamp=timestamp,
                    interface=interface.name,
                    event_type="link_up",
                    severity="info",
                    message=f"{interface.name} link came up",
                )
            ]

        return []

    def _counter_events(
        self,
        timestamp: float,
        interface: InterfaceStats,
        state: _InterfaceHealthState,
    ) -> list[HealthEvent]:
        events: list[HealthEvent] = []
        err_delta = (
            (interface.errin - state.errin)
            + (interface.errout - state.errout)
        )
        drop_delta = (
            (interface.dropin - state.dropin)
            + (interface.dropout - state.dropout)
        )

        if state.is_up is None:
            return []

        if err_delta >= self.error_delta_threshold:
            events.append(
                HealthEvent(
                    timestamp=timestamp,
                    interface=interface.name,
                    event_type="high_errors",
                    severity="warning",
                    message=(
                        f"{interface.name} reported {err_delta} new errors "
                        "since last sample"
                    ),
                    value=float(err_delta),
                )
            )

        if drop_delta >= self.drop_delta_threshold:
            events.append(
                HealthEvent(
                    timestamp=timestamp,
                    interface=interface.name,
                    event_type="high_drops",
                    severity="warning",
                    message=(
                        f"{interface.name} reported {drop_delta} new drops "
                        "since last sample"
                    ),
                    value=float(drop_delta),
                )
            )

        return events
