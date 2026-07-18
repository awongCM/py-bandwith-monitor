"""Remote agent loop: sample locally and POST to the hub."""

from __future__ import annotations

import logging
import socket
import time
from typing import Iterable

import httpx

from monitor.collector import BandwidthCollector, list_interface_stats
from monitor.models import AggregateRates, InterfaceStats

logger = logging.getLogger(__name__)


def resolve_agent_host_id(explicit: str | None) -> str:
    if explicit is not None and explicit.strip():
        return explicit.strip()
    return socket.gethostname()


def build_agent_payload(
    host_id: str,
    sample: AggregateRates,
    snapshots: list[InterfaceStats],
) -> dict:
    return {
        "host_id": host_id,
        "timestamp": sample.timestamp,
        "recv_bps": sample.recv_bps,
        "sent_bps": sample.sent_bps,
        "interfaces": [item.to_dict() for item in sample.interfaces],
        "snapshots": [item.to_dict() for item in snapshots],
    }


def post_sample(
    client: httpx.Client,
    *,
    server: str,
    token: str,
    payload: dict,
) -> None:
    url = server.rstrip("/") + "/api/agents/samples"
    response = client.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()


def run_agent(
    *,
    server: str,
    token: str,
    host_id: str,
    interval: float = 1.0,
    history_size: int = 3600,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    duration: float | None = None,
    samples: int | None = None,
) -> int:
    collector = BandwidthCollector(
        interval=interval,
        history_size=history_size,
        include=include,
        exclude=exclude,
    )
    deadline = time.monotonic() + duration if duration is not None else None
    count = 0
    with httpx.Client() as client:
        for sample in collector.watch():
            snapshots = list_interface_stats(include=include, exclude=exclude)
            payload = build_agent_payload(host_id, sample, snapshots)
            try:
                post_sample(client, server=server, token=token, payload=payload)
            except Exception:
                logger.exception("Failed to post sample to %s", server)
            count += 1
            if samples is not None and count >= samples:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
    return 0
