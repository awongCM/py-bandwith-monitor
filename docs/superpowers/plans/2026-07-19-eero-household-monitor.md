# Eero Household Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an isolated sibling package `eero_monitor` that lists household Eero devices and shows live/historical per-device rates via CLI and a FastAPI dashboard on port 8081.

**Architecture:** Greenfield package mirroring Phase 2-style `monitor` (auth → SDK client → collector → SQLite/health/service → FastAPI + static UI). No imports from/to `monitor`. Rates are router-reported instantaneous values (not kernel deltas). CI uses a mocked client only.

**Tech Stack:** Python 3.10+ (live `eero-api` SDK needs 3.12+), SQLite, FastAPI, uvicorn, unofficial `eero-api` SDK (lazy import), Chart.js dashboard.

**Spec:** `docs/superpowers/specs/2026-07-18-eero-monitor-design.md`

## Global Constraints

- Zero imports between `eero_monitor` and `monitor` (and vice versa)
- Separate DB default `eero_monitor.db`; separate bind `127.0.0.1:8081`
- Auth via `EERO_SESSION` + `EERO_NETWORK_ID` only; no interactive login
- SDK types must not leak past `client.py`
- Default poll interval **5.0** seconds; retention default **7** days
- Offline / missing rates → `0.0` bps; aggregate sentinel `__total__`; API events use device_id `__api__`
- No YAML config, rollups, alerts, or agent ingest in v1
- No live Eero calls in CI; mock `EeroClient` / inject fake client
- TDD: failing test → implement → pass → commit per task
- `requirements.txt` stays monitor-only; eero deps go in `requirements-eero.txt`

## File structure

| File | Responsibility |
|------|----------------|
| `eero_monitor/__init__.py` | Package version |
| `eero_monitor/__main__.py` | `python -m eero_monitor` entry |
| `eero_monitor/auth.py` | Read/validate `EERO_*` env |
| `eero_monitor/models.py` | Device dataclasses + sentinels |
| `eero_monitor/formatting.py` | Local `bytes2human` / `rate2human` (no shared package) |
| `eero_monitor/client.py` | Thin SDK wrapper → `DeviceSnapshot` (+ rates) |
| `eero_monitor/collector.py` | Poll client → `AggregateDeviceRates` |
| `eero_monitor/health.py` | Online/offline + API/auth events |
| `eero_monitor/storage.py` | SQLite samples / snapshots / health / retention |
| `eero_monitor/service.py` | Background sampler + WS bridge |
| `eero_monitor/server.py` | FastAPI REST + WebSocket + static |
| `eero_monitor/cli.py` | `devices` / `watch` / `serve` |
| `eero_monitor/static/*` | Dashboard HTML/JS/CSS |
| `requirements-eero.txt` | Optional deps |
| `tests/eero_tests/*` | Isolated tests (named to avoid shadowing `eero_monitor`) |
| `README.md` | Optional Eero section |

---

### Task 1: Package skeleton, auth, models, mocked client

**Files:**
- Create: `eero_monitor/__init__.py`
- Create: `eero_monitor/auth.py`
- Create: `eero_monitor/models.py`
- Create: `eero_monitor/formatting.py`
- Create: `eero_monitor/client.py`
- Create: `tests/eero_monitor/__init__.py`
- Create: `tests/eero_monitor/test_auth.py`
- Create: `tests/eero_monitor/test_client.py`
- Create: `tests/eero_monitor/test_isolation.py`
- Create: `requirements-eero.txt`

**Interfaces:**
- Consumes: none
- Produces:
  - `eero_monitor.__version__: str`
  - `AGGREGATE_DEVICE = "__total__"`, `API_DEVICE_ID = "__api__"`
  - `@dataclass(frozen=True) DeviceSnapshot` / `DeviceRates` / `AggregateDeviceRates` / `HealthEvent` with `to_dict()`
  - `class AuthError(Exception)`
  - `load_credentials() -> tuple[str, str]` — `(session, network_id)`; raises `AuthError` if missing/empty
  - `class EeroClient` with `__init__(session: str, network_id: str, *, transport=None)` and `list_devices() -> list[DeviceSnapshot]`
  - `EeroClient` also exposes rate fields via optional attributes on snapshots **or** a parallel map; prefer embedding instantaneous rates by returning devices and a method `instant_rates(devices) -> dict[str, tuple[float, float]]` keyed by `device_id` (recv_bps, sent_bps). Simpler v1: store rates on a companion dataclass produced only inside client — **use** `list_devices() -> list[DeviceSnapshot]` where rates are **not** on snapshot; add `EeroClient.fetch_rates() -> list[tuple[DeviceSnapshot, float, float]]` returning `(snapshot, recv_bps, sent_bps)`.
  - Chosen API (lock this): `EeroClient.list_device_samples() -> list[tuple[DeviceSnapshot, float, float]]` where floats are recv/sent bps (0.0 when unknown).
  - `bytes2human`, `rate2human` in `formatting.py` (copy logic from `monitor.formatting` without importing `monitor` or `psutil`)

- [ ] **Step 1: Write failing auth + isolation + client mapping tests**

Create `tests/eero_monitor/test_auth.py`:

```python
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from eero_monitor.auth import AuthError, load_credentials


class AuthTests(unittest.TestCase):
    def test_missing_both_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AuthError) as ctx:
                load_credentials()
        message = str(ctx.exception)
        self.assertIn("EERO_SESSION", message)
        self.assertIn("EERO_NETWORK_ID", message)

    def test_partial_env_raises(self) -> None:
        with patch.dict(os.environ, {"EERO_SESSION": "tok"}, clear=True):
            with self.assertRaises(AuthError):
                load_credentials()

    def test_loads_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {"EERO_SESSION": "tok", "EERO_NETWORK_ID": "net1"},
            clear=True,
        ):
            self.assertEqual(load_credentials(), ("tok", "net1"))
```

Create `tests/eero_monitor/test_isolation.py`:

```python
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EERO_DIR = ROOT / "eero_monitor"


class IsolationTests(unittest.TestCase):
    def test_eero_monitor_does_not_import_monitor(self) -> None:
        offenders: list[str] = []
        for path in EERO_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "monitor" or alias.name.startswith("monitor."):
                            offenders.append(f"{path}:{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "monitor" or mod.startswith("monitor."):
                        offenders.append(f"{path}:{mod}")
        self.assertEqual(offenders, [])
```

Create `tests/eero_monitor/test_client.py`:

```python
from __future__ import annotations

import unittest
from typing import Any

from eero_monitor.client import EeroClient
from eero_monitor.models import DeviceSnapshot


class FakeTransport:
    def __init__(self, devices: list[dict[str, Any]]) -> None:
        self.devices = devices

    def fetch_devices(self, network_id: str) -> list[dict[str, Any]]:
        assert network_id == "net1"
        return self.devices


class ClientMappingTests(unittest.TestCase):
    def test_maps_devices_and_rates(self) -> None:
        transport = FakeTransport(
            [
                {
                    "url": "/2.2/devices/aaa",
                    "nickname": "Phone",
                    "hostname": "phone",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "ip": "192.168.1.10",
                    "connected": True,
                    "connection_type": "wireless",
                    "wireless_bitrate_down": 1_000_000,
                    "wireless_bitrate_up": 200_000,
                },
                {
                    "url": "/2.2/devices/bbb",
                    "hostname": "tv",
                    "mac": "11:22:33:44:55:66",
                    "connected": False,
                },
            ]
        )
        client = EeroClient("tok", "net1", transport=transport)
        samples = client.list_device_samples()
        self.assertEqual(len(samples), 2)
        phone, recv, sent = samples[0]
        self.assertIsInstance(phone, DeviceSnapshot)
        self.assertEqual(phone.device_id, "aaa")
        self.assertEqual(phone.name, "Phone")
        self.assertTrue(phone.is_online)
        self.assertEqual(phone.connection, "wifi")
        self.assertEqual(recv, 1_000_000.0)
        self.assertEqual(sent, 200_000.0)
        tv, recv2, sent2 = samples[1]
        self.assertEqual(tv.device_id, "bbb")
        self.assertEqual(tv.name, "tv")
        self.assertFalse(tv.is_online)
        self.assertEqual(recv2, 0.0)
        self.assertEqual(sent2, 0.0)

    def test_skips_bad_device_entries(self) -> None:
        transport = FakeTransport(
            [
                {"url": "/2.2/devices/ok", "nickname": "Ok", "connected": True},
                None,  # type: ignore[list-item]
                {"connected": True},  # missing id
            ]
        )
        client = EeroClient("tok", "net1", transport=transport)
        samples = client.list_device_samples()
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0][0].device_id, "ok")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.eero_monitor.test_auth tests.eero_monitor.test_client tests.eero_monitor.test_isolation -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'eero_monitor'` (or import errors).

- [ ] **Step 3: Implement package skeleton**

`eero_monitor/__init__.py`:

```python
"""Optional household device monitor via the unofficial Eero cloud API."""

__version__ = "0.1.0"
```

(`__main__.py` and `cli.py` arrive in Task 2.)

`eero_monitor/auth.py`:

```python
from __future__ import annotations

import os


class AuthError(Exception):
    """Missing or invalid Eero credentials."""


def load_credentials() -> tuple[str, str]:
    session = (os.environ.get("EERO_SESSION") or "").strip()
    network_id = (os.environ.get("EERO_NETWORK_ID") or "").strip()
    if not session or not network_id:
        raise AuthError(
            "Missing Eero credentials. Set EERO_SESSION and EERO_NETWORK_ID "
            "(see README: Optional Eero household monitor)."
        )
    return session, network_id
```

`eero_monitor/models.py` — frozen dataclasses per spec §6.3 with `to_dict()` via `dataclasses.asdict`. Include:

```python
AGGREGATE_DEVICE = "__total__"
API_DEVICE_ID = "__api__"
```

`eero_monitor/formatting.py` — copy `bytes2human` / `rate2human` from `monitor/formatting.py` but **omit** `duplex_label` and any `psutil` import.

`eero_monitor/client.py`:

```python
from __future__ import annotations

from typing import Any, Protocol

from eero_monitor.models import DeviceSnapshot


class DeviceTransport(Protocol):
    def fetch_devices(self, network_id: str) -> list[dict[str, Any]]: ...


class EeroClient:
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
            snapshot, recv_bps, sent_bps = mapped
            samples.append((snapshot, recv_bps, sent_bps))
        return samples

    def _fetch_raw(self) -> list[dict[str, Any]]:
        if self._transport is not None:
            return list(self._transport.fetch_devices(self.network_id))
        return self._fetch_via_sdk()

    def _fetch_via_sdk(self) -> list[dict[str, Any]]:
        # Lazy import; raise clear error if eero-api missing.
        # Implementation may use asyncio.run(...) for async SDK.
        # Map SDK objects to plain dicts before returning.
        raise NotImplementedError("Live SDK path wired in later smoke-test; tests use transport=")
```

Implement `_map_device(raw: Any) -> tuple[DeviceSnapshot, float, float] | None` defensively:

- `device_id` from last path segment of `url` / `url` field / `id`
- `name` = nickname or hostname or mac or device_id
- `connection`: map `wireless`→`wifi`, `wired`→`wired`, else `unknown`
- rates from `wireless_bitrate_down` / `wireless_bitrate_up` or `usage` down/up if present; else 0.0; if not `connected`, force 0.0
- skip entries that are not dicts or lack an id

For live SDK (implement a minimal path so serve can work later):

```python
def _fetch_via_sdk(self) -> list[dict[str, Any]]:
    try:
        import eero  # package name from eero-api; adjust if import path differs
    except ImportError as exc:
        raise RuntimeError(
            "eero-api is required for live Eero access. "
            "pip install -r requirements-eero.txt"
        ) from exc
    # Use session cookie; call devices endpoint for network_id.
    # Prefer sync helpers if available; else asyncio.run(async_call).
    # Return list[dict].
```

Pin in `requirements-eero.txt`:

```text
eero-api>=5,<6
fastapi>=0.110,<1
uvicorn[standard]>=0.27,<1
httpx>=0.27,<1
```

Note in a comment at top of file: live SDK requires Python 3.12+.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.eero_monitor.test_auth tests.eero_monitor.test_client tests.eero_monitor.test_isolation -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add eero_monitor tests/eero_monitor requirements-eero.txt
git commit -m "feat(eero): add package skeleton, auth, models, and mocked client"
```

---

### Task 2: Collector + CLI `devices` / `watch`

**Files:**
- Create: `eero_monitor/collector.py`
- Create: `eero_monitor/cli.py`
- Create: `eero_monitor/__main__.py`
- Create: `tests/eero_monitor/test_collector.py`
- Create: `tests/eero_monitor/test_cli.py`

**Interfaces:**
- Consumes: `EeroClient.list_device_samples`, models, auth, formatting
- Produces:
  - `class DeviceCollector` with `__init__(client, *, interval: float = 5.0)` and `sample() -> AggregateDeviceRates` and `watch(*, stop_check=None, duration=None, samples=None) -> Iterator[AggregateDeviceRates]`
  - `cli.main(argv: Sequence[str] | None = None) -> int`
  - Commands: `devices` (`--json`), `watch` (`--interval`, `--json`, `--duration`, `--samples`)

- [ ] **Step 1: Write failing collector + CLI tests**

`tests/eero_monitor/test_collector.py`:

```python
from __future__ import annotations

import unittest

from eero_monitor.collector import DeviceCollector
from eero_monitor.models import DeviceSnapshot


class FakeClient:
    def __init__(self, samples):
        self._samples = samples

    def list_device_samples(self):
        return self._samples


class CollectorTests(unittest.TestCase):
    def test_aggregates_online_and_offline(self) -> None:
        samples = [
            (
                DeviceSnapshot(
                    device_id="a",
                    name="A",
                    mac=None,
                    ip=None,
                    is_online=True,
                    connection="wifi",
                ),
                100.0,
                50.0,
            ),
            (
                DeviceSnapshot(
                    device_id="b",
                    name="B",
                    mac=None,
                    ip=None,
                    is_online=False,
                    connection="unknown",
                ),
                0.0,
                0.0,
            ),
        ]
        collector = DeviceCollector(FakeClient(samples), interval=5.0)
        aggregate = collector.sample()
        self.assertEqual(aggregate.recv_bps, 100.0)
        self.assertEqual(aggregate.sent_bps, 50.0)
        self.assertEqual(len(aggregate.devices), 2)

    def test_empty_device_list(self) -> None:
        collector = DeviceCollector(FakeClient([]), interval=5.0)
        aggregate = collector.sample()
        self.assertEqual(aggregate.recv_bps, 0.0)
        self.assertEqual(aggregate.devices, ())
```

`tests/eero_monitor/test_cli.py`:

```python
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from eero_monitor import cli
from eero_monitor.models import DeviceSnapshot


class FakeClient:
    def list_device_samples(self):
        return [
            (
                DeviceSnapshot(
                    device_id="a",
                    name="Phone",
                    mac="aa:bb",
                    ip="1.2.3.4",
                    is_online=True,
                    connection="wifi",
                ),
                10.0,
                5.0,
            )
        ]


class CliTests(unittest.TestCase):
    def test_devices_json(self) -> None:
        with patch.dict(
            os.environ,
            {"EERO_SESSION": "t", "EERO_NETWORK_ID": "n"},
            clear=True,
        ), patch("eero_monitor.cli.EeroClient", return_value=FakeClient()):
            code = cli.main(["devices", "--json"])
        self.assertEqual(code, 0)

    def test_missing_credentials_exits_nonzero(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            code = cli.main(["devices"])
        self.assertNotEqual(code, 0)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m unittest tests.eero_monitor.test_collector tests.eero_monitor.test_cli -v`

- [ ] **Step 3: Implement collector + CLI**

`DeviceCollector.sample()`:

1. Call `client.list_device_samples()`
2. Build `DeviceRates` per device (timestamp=`time.time()`, rates from tuple)
3. Return `AggregateDeviceRates` with sums and tuple of device rates

`watch()`: loop with `time.sleep(interval)` (skip sleep before first sample), honor `duration` / `samples` / `stop_check` like `monitor.collector.BandwidthCollector.watch`.

CLI `devices`: load credentials → client → print table or JSON list of snapshots (+ rates optional in JSON).

CLI `watch`: print human lines via `rate2human` or JSON of `aggregate.to_dict()` each sample.

Wire `__main__.py` to `cli.main`.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(eero): add device collector and devices/watch CLI"
```

---

### Task 3: Storage, health, and sampling service

**Files:**
- Create: `eero_monitor/storage.py`
- Create: `eero_monitor/health.py`
- Create: `eero_monitor/service.py`
- Create: `tests/eero_monitor/test_storage.py`
- Create: `tests/eero_monitor/test_health.py`
- Create: `tests/eero_monitor/test_service.py`

**Interfaces:**
- Consumes: models, collector, client
- Produces:
  - `class MetricsDatabase` with schema:
    - `rate_samples(timestamp, device_id, recv_bps, sent_bps)`
    - `device_snapshots(timestamp, device_id, name, mac, ip, is_online, connection, signal, last_seen)` — keep latest per device via replace or “latest query”
    - `health_events(timestamp, device_id, event_type, severity, message, value)`
  - Methods: `insert_rates(AggregateDeviceRates)`, `insert_device_snapshots(timestamp, list[DeviceSnapshot])`, `insert_health_event(HealthEvent)`, `get_rate_history(device_id, *, minutes)`, `get_overview(*, minutes=5)`, `get_latest_device_rates()`, `get_latest_device_snapshots()`, `get_health_events(*, limit=50)`, `purge_older_than(days: float)`
  - `class HealthMonitor` with `evaluate(timestamp, devices: list[DeviceSnapshot]) -> list[HealthEvent]` for online↔offline; plus helpers `auth_error_event` / `api_error_event`
  - `class SamplingService` background thread: each tick sample → persist rates/snapshots/health → optional `on_sample` callback; periodic retention purge
  - `class WebSocketBridge` (same pattern as monitor: threadsafe queue / `call_soon_threadsafe` publish)

Schema DDL (lock):

```sql
CREATE TABLE IF NOT EXISTS rate_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    device_id TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eero_rate_device_ts
    ON rate_samples(device_id, timestamp);

CREATE TABLE IF NOT EXISTS device_snapshots (
    device_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    name TEXT NOT NULL,
    mac TEXT,
    ip TEXT,
    is_online INTEGER NOT NULL,
    connection TEXT NOT NULL,
    signal REAL,
    last_seen REAL
);

CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);
CREATE INDEX IF NOT EXISTS idx_eero_health_ts
    ON health_events(timestamp DESC);
```

`insert_rates`: write `__total__` row from aggregate totals plus one row per device.

- [ ] **Step 1: Write failing storage/health/service tests**

Cover: insert + history by device + `__total__`; retention purge removes old rows; health emits offline when `is_online` flips; service persists one sample with a fake client.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement storage, health, service**

Mirror threading/lock patterns from `monitor/storage.py` and `monitor/service.py` but **without** `host_id`, rollups, or alerts. Retention: delete `rate_samples` / `health_events` older than `retention_days` (default 7); snapshots are latest-only so no purge needed (or delete stale device rows not seen in N days — optional; v1 can leave snapshots).

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(eero): add SQLite storage, health monitor, and sampling service"
```

---

### Task 4: FastAPI server, WebSocket, and dashboard

**Files:**
- Create: `eero_monitor/server.py`
- Create: `eero_monitor/static/index.html`
- Create: `eero_monitor/static/app.js`
- Create: `eero_monitor/static/styles.css`
- Modify: `eero_monitor/cli.py` (add `serve`)
- Create: `tests/eero_monitor/test_server.py`

**Interfaces:**
- Consumes: storage, service, auth, client
- Produces:
  - `create_app(*, db_path, interval=5.0, history_size=3600, retention_days=7, client: EeroClient | None = None) -> FastAPI`
  - Endpoints per spec §7
  - CLI `serve --host --port --db --interval --history-size --retention-days`

- [ ] **Step 1: Write failing API tests with TestClient + fake service/DB**

Seed DB with rates/devices/health; assert JSON shapes for `/api/overview`, `/api/history`, `/api/devices`, `/api/health`. Inject a client that returns empty/static samples so lifespan can start.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement server + static UI**

Adapt `monitor/static` patterns: aggregate sparkline, device table, device selector, 5/15/60 minute charts, health panel. Banner text: **Household (via Eero)** — not local NICs.

Default bind in CLI: host `127.0.0.1`, port `8081`.

On auth/API errors from collector, service records health events and continues.

- [ ] **Step 4: Run — expect PASS** (all eero_monitor tests)

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(eero): add FastAPI dashboard and serve command"
```

---

### Task 5: README, requirements polish, full isolation verification

**Files:**
- Modify: `README.md`
- Modify: `requirements-eero.txt` (ensure pins complete)
- Modify: `tests/eero_monitor/test_isolation.py` if needed

- [ ] **Step 1: Write failing doc smoke check (optional)** — or skip to implementation; README has no unit test required. Prefer: extend isolation test to also scan that `monitor/` does not import `eero_monitor`.

```python
def test_monitor_does_not_import_eero_monitor(self) -> None:
    # same AST walk under monitor/
```

- [ ] **Step 2: Run isolation test — expect FAIL until assertion added/passes**

- [ ] **Step 3: Update README**

Add section **Optional: Eero household monitor**:

- Purpose (household devices via unofficial Eero API)
- `pip install -r requirements-eero.txt`
- Python 3.12+ note for live `eero-api`
- `export EERO_SESSION=...` / `EERO_NETWORK_ID=...`
- Commands: `devices`, `watch`, `serve` (port 8081)
- Unofficial API disclaimer; Amazon-login caveat (secondary admin email/password if needed)
- Replacing Eero → stop/remove `eero_monitor`; keep `monitor`
- Update deferred row in features table from “deferred” to available/optional

- [ ] **Step 4: Run full eero + monitor suites**

```bash
python -m unittest discover -s tests -v
```

Expected: PASS (monitor unchanged; eero tests green).

- [ ] **Step 5: Commit**

```bash
git commit -m "docs: document optional Eero household monitor"
```

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| G1 CLI + dashboard | 2, 4 |
| G2 SQLite + WS | 3, 4 |
| G3 SDK behind client | 1 |
| G4 Env auth | 1, 2 |
| G5 All devices, 0 bps offline | 1, 2 |
| G6 Isolation | 1, 5 |
| G7 README optional | 5 |
| Error handling §8 | 2–4 |
| Success criteria §12 | 2–5 |

## Open details resolved in this plan

| Topic | Choice |
|-------|--------|
| PyPI SDK | `eero-api>=5,<6` (lazy import; transport injection for tests) |
| SQLite DDL | See Task 3 (raw samples only; no rollups) |
| Client API | `list_device_samples() -> list[tuple[DeviceSnapshot, float, float]]` |
| Live field names | Prefer bitrate/usage keys above; adjust mapping when live fixtures exist without breaking FakeTransport tests |
