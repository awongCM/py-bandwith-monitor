"""Thin wrapper over the unofficial Eero SDK / injectable transport."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Protocol

from eero_monitor.models import DeviceSnapshot

_EERO_SDK_HELP = (
    "eero-api is required for live Eero access. "
    "Install with: pip install -r requirements-eero.txt (Python 3.12+). "
    "Use the same interpreter for login and serve, e.g. "
    "source .venv-eero/bin/activate && python -m eero_monitor login"
)


def ensure_eero_sdk() -> None:
    """Fail fast when the current interpreter cannot import ``eero-api``."""
    try:
        import eero  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            f"{_EERO_SDK_HELP}\nCurrent interpreter: {sys.executable}"
        ) from exc


class DeviceTransport(Protocol):
    def fetch_devices(self, network_id: str) -> list[dict[str, Any]]: ...


class EeroClient:
    """Map Eero device payloads into :class:`DeviceSnapshot` + rates."""

    def __init__(
        self,
        session: str,
        network_id: str,
        *,
        transport: DeviceTransport | None = None,
    ) -> None:
        self.session = session
        self.network_id = network_id
        self._transport = transport

    def list_device_samples(self) -> list[tuple[DeviceSnapshot, float, float]]:
        raw_devices = self._fetch_raw()
        samples: list[tuple[DeviceSnapshot, float, float]] = []
        for raw in raw_devices:
            mapped = _map_device(raw)
            if mapped is None:
                continue
            samples.append(mapped)
        return samples

    def _fetch_raw(self) -> list[dict[str, Any]]:
        if self._transport is not None:
            return list(self._transport.fetch_devices(self.network_id))
        return self._fetch_via_sdk()

    def _fetch_via_sdk(self) -> list[dict[str, Any]]:
        try:
            from eero import EeroClient as SdkClient
        except ImportError as exc:  # pragma: no cover - exercised live only
            raise RuntimeError(_EERO_SDK_HELP) from exc

        async def _load() -> list[dict[str, Any]]:
            async with SdkClient() as client:
                await client.set_session_token(self.session)
                response = await client.get_devices(network_id=self.network_id)
            data = response.get("data", response) if isinstance(response, dict) else response
            if isinstance(data, dict):
                data = data.get("devices") or data.get("data") or []
            if not isinstance(data, list):
                return []
            return [_normalize_sdk_device(item) for item in data]

        return asyncio.run(_load())


def _normalize_sdk_device(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return dict(item.model_dump())
    if hasattr(item, "__dict__"):
        return dict(vars(item))
    return {"raw": item}


def _map_device(raw: Any) -> tuple[DeviceSnapshot, float, float] | None:
    if not isinstance(raw, dict):
        return None

    device_id = _extract_device_id(raw)
    if not device_id:
        return None

    is_online = bool(raw.get("connected") or raw.get("is_connected") or False)
    name = (
        _as_optional_str(raw.get("nickname"))
        or _as_optional_str(raw.get("hostname"))
        or _as_optional_str(raw.get("mac"))
        or device_id
    )
    connection = _map_connection(raw.get("connection_type") or raw.get("connection"))
    recv_bps, sent_bps = _extract_rates(raw)
    if not is_online:
        recv_bps, sent_bps = 0.0, 0.0

    snapshot = DeviceSnapshot(
        device_id=device_id,
        name=name,
        mac=_as_optional_str(raw.get("mac")),
        ip=_as_optional_str(raw.get("ip") or raw.get("ip_address")),
        is_online=is_online,
        connection=connection,
        signal=_as_optional_float(raw.get("signal") or raw.get("wifi_signal")),
        last_seen=_as_optional_float(raw.get("last_active") or raw.get("last_seen")),
    )
    return snapshot, recv_bps, sent_bps


def _extract_device_id(raw: dict[str, Any]) -> str | None:
    for key in ("device_id", "id", "serial"):
        value = _as_optional_str(raw.get(key))
        if value:
            return value
    url = _as_optional_str(raw.get("url"))
    if url:
        return url.rstrip("/").split("/")[-1] or None
    return None


def _map_connection(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"wireless", "wifi", "wi-fi"}:
        return "wifi"
    if text in {"wired", "ethernet"}:
        return "wired"
    return "unknown"


def _extract_rates(raw: dict[str, Any]) -> tuple[float, float]:
    usage = raw.get("usage")
    if isinstance(usage, dict):
        down = usage.get("down") or usage.get("download")
        up = usage.get("up") or usage.get("upload")
        if down is not None or up is not None:
            return _as_float(down), _as_float(up)

    return (
        _as_float(
            raw.get("wireless_bitrate_down")
            or raw.get("down_mbps")
            or raw.get("rx_bitrate")
        ),
        _as_float(
            raw.get("wireless_bitrate_up")
            or raw.get("up_mbps")
            or raw.get("tx_bitrate")
        ),
    )


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
