"""Thin wrapper over the unofficial Eero SDK / injectable transport."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from eero_monitor.models import DeviceSnapshot

_EERO_SDK_HELP = (
    "eero-api is required for live Eero access. "
    "Install with: pip install -r requirements-eero.txt (Python 3.12+). "
    "Use the same interpreter for login and serve, e.g. "
    "source .venv-eero/bin/activate && python -m eero_monitor login"
)

# device_id -> (timestamp, download_bytes, upload_bytes)
_CounterState = dict[str, tuple[float, float, float]]
_UTC = timezone.utc


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
    """Map Eero device payloads into :class:`DeviceSnapshot` + live rates."""

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
        self._last_data_usage: _CounterState = {}
        self._network_timezone: str | None = None

    def list_device_samples(self) -> list[tuple[DeviceSnapshot, float, float]]:
        timestamp = time.time()
        raw_devices, usage_totals = self._fetch_poll()
        samples: list[tuple[DeviceSnapshot, float, float]] = []
        for raw in raw_devices:
            snapshot = _map_device_snapshot(raw)
            if snapshot is None:
                continue
            recv_bps, sent_bps = 0.0, 0.0
            if snapshot.is_online:
                recv_bps, sent_bps = _extract_usage_rates(raw)
                if recv_bps <= 0.0 and sent_bps <= 0.0:
                    totals = usage_totals.get(snapshot.device_id)
                    if totals is not None:
                        recv_bps, sent_bps = _counter_delta_bps(
                            f"data_usage:{snapshot.device_id}",
                            timestamp,
                            totals[0],
                            totals[1],
                            self._last_data_usage,
                        )
            samples.append((snapshot, recv_bps, sent_bps))
        return samples

    def _fetch_poll(self) -> tuple[list[dict[str, Any]], dict[str, tuple[float, float]]]:
        if self._transport is not None:
            raw_devices = list(self._transport.fetch_devices(self.network_id))
            totals: dict[str, tuple[float, float]] = {}
            fetch_totals = getattr(self._transport, "fetch_usage_totals", None)
            if callable(fetch_totals):
                for device_id, payload in fetch_totals(self.network_id).items():
                    totals[device_id] = _extract_data_usage_bytes(payload)
            return raw_devices, totals
        return self._fetch_via_sdk()

    def _fetch_via_sdk(self) -> tuple[list[dict[str, Any]], dict[str, tuple[float, float]]]:
        try:
            from eero import EeroClient as SdkClient
        except ImportError as exc:  # pragma: no cover - exercised live only
            raise RuntimeError(_EERO_SDK_HELP) from exc

        async def _load() -> tuple[list[dict[str, Any]], dict[str, tuple[float, float]]]:
            async with SdkClient() as client:
                await client.set_session_token(self.session)
                response = await client.get_devices(
                    network_id=self.network_id,
                    refresh_cache=True,
                )
                raw_devices = _parse_device_list(response)
                raw_devices = await _enrich_zero_usage_devices(
                    client,
                    self.network_id,
                    raw_devices,
                )
                usage_totals = await self._fetch_data_usage_totals(client)
            return raw_devices, usage_totals

        return asyncio.run(_load())

    async def _fetch_data_usage_totals(self, client: Any) -> dict[str, tuple[float, float]]:
        timezone_name = await self._resolve_network_timezone(client)
        payload = _day_data_usage_payload(timezone_name)
        try:
            response = await client.get_data_usage(
                self.network_id,
                payload,
                resource="devices",
            )
        except Exception:  # pragma: no cover - live API variance
            return {}

        totals: dict[str, tuple[float, float]] = {}
        values = response.get("data", {}).get("values", [])
        if not isinstance(values, list):
            return totals
        for item in values:
            if not isinstance(item, dict):
                continue
            device_id = _extract_device_id(item)
            if device_id:
                totals[device_id] = _extract_data_usage_bytes(item)
        return totals

    async def _resolve_network_timezone(self, client: Any) -> str:
        if self._network_timezone:
            return self._network_timezone
        timezone_name = "UTC"
        try:
            response = await client.get_network(self.network_id)
            data = response.get("data", response)
            if isinstance(data, dict):
                timezone_field = data.get("timezone")
                if isinstance(timezone_field, dict):
                    timezone_name = (
                        _as_optional_str(timezone_field.get("value"))
                        or _as_optional_str(timezone_field.get("name"))
                        or timezone_name
                    )
                elif timezone_field:
                    timezone_name = str(timezone_field)
        except Exception:  # pragma: no cover - live API variance
            pass
        try:
            ZoneInfo(timezone_name)
        except Exception:
            timezone_name = "UTC"
        self._network_timezone = timezone_name
        return timezone_name


async def _enrich_zero_usage_devices(
    client: Any,
    network_id: str,
    raw_devices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Refresh per-device detail when the list payload reports zero live usage."""

    async def _enrich(raw: dict[str, Any]) -> dict[str, Any]:
        snapshot = _map_device_snapshot(raw)
        if snapshot is None or not snapshot.is_online:
            return raw
        recv_bps, sent_bps = _extract_usage_rates(raw)
        if recv_bps > 0.0 or sent_bps > 0.0:
            return raw
        try:
            detail = await client.get_device(
                snapshot.device_id,
                network_id=network_id,
                refresh_cache=True,
            )
        except Exception:  # pragma: no cover - live API variance
            return raw
        data = detail.get("data", detail)
        if not isinstance(data, dict):
            return raw
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return raw
        merged = dict(raw)
        merged["usage"] = usage
        return merged

    return list(await asyncio.gather(*[_enrich(item) for item in raw_devices]))


def _day_data_usage_payload(timezone_name: str) -> dict[str, str]:
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return {
        "start": start.astimezone(_UTC).replace(tzinfo=None).isoformat() + "Z",
        "end": end.astimezone(_UTC).replace(tzinfo=None).isoformat() + "Z",
        "cadence": "hourly",
        "timezone": timezone_name,
    }


def _extract_data_usage_bytes(payload: dict[str, Any]) -> tuple[float, float]:
    return _as_float(payload.get("download")), _as_float(payload.get("upload"))


def _counter_delta_bps(
    key: str,
    timestamp: float,
    download_bytes: float,
    upload_bytes: float,
    last: _CounterState,
) -> tuple[float, float]:
    previous = last.get(key)
    last[key] = (timestamp, download_bytes, upload_bytes)
    if previous is None:
        return 0.0, 0.0

    prev_ts, prev_download, prev_upload = previous
    elapsed = timestamp - prev_ts
    if elapsed <= 0:
        return 0.0, 0.0

    download_delta = download_bytes - prev_download
    upload_delta = upload_bytes - prev_upload
    if download_delta < 0 or upload_delta < 0:
        return 0.0, 0.0

    return (download_delta * 8) / elapsed, (upload_delta * 8) / elapsed


def _parse_device_list(response: Any) -> list[dict[str, Any]]:
    data = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(data, dict):
        data = data.get("devices") or data.get("data") or []
    if not isinstance(data, list):
        return []
    return [_normalize_sdk_device(item) for item in data]


def _normalize_sdk_device(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return dict(item.model_dump())
    if hasattr(item, "__dict__"):
        return dict(vars(item))
    return {"raw": item}


def _map_device_snapshot(raw: Any) -> DeviceSnapshot | None:
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

    return DeviceSnapshot(
        device_id=device_id,
        name=name,
        mac=_as_optional_str(raw.get("mac")),
        ip=_as_optional_str(raw.get("ip") or raw.get("ip_address")),
        is_online=is_online,
        connection=connection,
        signal=_as_optional_float(raw.get("signal") or raw.get("wifi_signal")),
        last_seen=_as_optional_float(raw.get("last_active") or raw.get("last_seen")),
    )


def _extract_usage_rates(raw: dict[str, Any]) -> tuple[float, float]:
    """Return live download/upload rates in bps from Eero ``usage`` fields."""
    usage = raw.get("usage")
    if isinstance(usage, dict):
        down_mbps = usage.get("down_mbps")
        up_mbps = usage.get("up_mbps")
        if down_mbps is not None or up_mbps is not None:
            return _as_float(down_mbps) * 1_000_000, _as_float(up_mbps) * 1_000_000

        down = usage.get("down") or usage.get("download")
        up = usage.get("up") or usage.get("upload")
        if down is not None or up is not None:
            return _as_float(down), _as_float(up)

    return 0.0, 0.0


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
