"""Validate samples posted by remote monitoring agents."""

from __future__ import annotations

from typing import Any

from monitor.models import AggregateRates, InterfaceRates, InterfaceStats


class IngestError(ValueError):
    """Invalid agent payload."""


def parse_agent_sample(
    body: dict[str, Any],
) -> tuple[str, AggregateRates, list[InterfaceStats]]:
    """Convert a remote agent payload into the monitor's data models."""
    host_id = str(body.get("host_id", "")).strip()
    if not host_id:
        raise IngestError("host_id is required")

    try:
        timestamp = float(body["timestamp"])
        recv_bps = float(body["recv_bps"])
        sent_bps = float(body["sent_bps"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestError(
            "timestamp, recv_bps, and sent_bps are required numbers"
        ) from exc

    interfaces_raw = body.get("interfaces") or []
    if not isinstance(interfaces_raw, list):
        raise IngestError("interfaces must be a list")
    interfaces: list[InterfaceRates] = []
    for item in interfaces_raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        try:
            interfaces.append(
                InterfaceRates(
                    name=str(item["name"]),
                    timestamp=float(item.get("timestamp", timestamp)),
                    recv_bps=float(item.get("recv_bps", 0.0)),
                    sent_bps=float(item.get("sent_bps", 0.0)),
                    recv_pps=float(item.get("recv_pps", 0.0)),
                    sent_pps=float(item.get("sent_pps", 0.0)),
                )
            )
        except (TypeError, ValueError) as exc:
            raise IngestError("interfaces must contain valid rate values") from exc

    snapshots_raw = body.get("snapshots") or []
    if not isinstance(snapshots_raw, list):
        raise IngestError("snapshots must be a list")
    snapshots: list[InterfaceStats] = []
    for item in snapshots_raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        try:
            snapshots.append(
                InterfaceStats(
                    name=str(item["name"]),
                    is_up=bool(item.get("is_up", True)),
                    speed_mbps=int(item.get("speed_mbps", 0)),
                    duplex=str(item.get("duplex", "unknown")),
                    mtu=int(item.get("mtu", 0)),
                    bytes_recv=int(item.get("bytes_recv", 0)),
                    bytes_sent=int(item.get("bytes_sent", 0)),
                    packets_recv=int(item.get("packets_recv", 0)),
                    packets_sent=int(item.get("packets_sent", 0)),
                    errin=int(item.get("errin", 0)),
                    errout=int(item.get("errout", 0)),
                    dropin=int(item.get("dropin", 0)),
                    dropout=int(item.get("dropout", 0)),
                )
            )
        except (TypeError, ValueError) as exc:
            raise IngestError("snapshots must contain valid interface values") from exc

    return (
        host_id,
        AggregateRates(
            timestamp=timestamp,
            recv_bps=recv_bps,
            sent_bps=sent_bps,
            interfaces=tuple(interfaces),
        ),
        snapshots,
    )
