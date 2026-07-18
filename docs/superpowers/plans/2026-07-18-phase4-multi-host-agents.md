# Phase 4 Multi-Host Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let other machines run `python -m monitor agent` and post NIC rates into the existing hub so the dashboard can select hosts.

**Architecture:** Namespace every SQLite row with `host_id`. The hub’s local sampler defaults to `"local"` (migration backfill). Agents POST aggregate + per-interface samples to `POST /api/agents/samples` with a shared bearer token. Read APIs take `?host=`; the dashboard adds a host selector.

**Tech Stack:** Python 3, SQLite, FastAPI, httpx, existing `psutil` collector, Chart.js dashboard.

**Spec:** `docs/superpowers/specs/2026-07-18-phase4-multi-host-agents-design.md`

## Global Constraints

- Same `monitor` package — no separate agent package, no `eero_monitor` coupling
- Shared bearer token only (`agents.token` / `MONITOR_AGENT_TOKEN`); ingest auth only
- Hub sampler default `host_id` is `"local"`; agent default is `socket.gethostname()`
- Read APIs / dashboard stay unauthenticated in v1
- Preserve raw `rate_samples` history across migration; rollup tables may be rebuilt
- Rate semantics unchanged (kernel counter deltas → bps)
- TDD: failing test → implement → pass → commit per task
- Do not implement Eero/router/SNMP in this plan

## File structure

| File | Responsibility |
|------|----------------|
| `monitor/models.py` | Add `LOCAL_HOST_ID`; optional `host_id` on health/alert events |
| `monitor/storage.py` | Schema + migration; all inserts/queries take `host_id` |
| `monitor/config.py` | `AgentsConfig`, `ServerConfig.host_id` |
| `monitor/service.py` | Tag local samples with `host_id`; include in WS payload |
| `monitor/ingest.py` | Parse/validate agent payloads; persist + alert + publish |
| `monitor/agent_client.py` | Collector loop that POSTs samples to hub |
| `monitor/server.py` | `?host=`, `/api/hosts`, `POST /api/agents/samples` |
| `monitor/cli.py` | `agent` subcommand |
| `monitor/static/index.html` / `app.js` / `styles.css` | Host selector + filter |
| `config.example.yaml` / `README.md` | Document agents |
| `tests/test_storage_hosts.py` | Migration + host isolation |
| `tests/test_ingest.py` | Auth + persist |
| `tests/test_agent_client.py` | HTTP client posting |
| `tests/test_config.py` | agents.token / host_id parsing |
| `tests/test_server.py` | API `?host=` + ingest routes |

---

### Task 1: `host_id` models, schema migration, and storage APIs

**Files:**
- Modify: `monitor/models.py`
- Modify: `monitor/storage.py`
- Create: `tests/test_storage_hosts.py`
- Modify: `tests/test_storage.py` (pass if defaults keep existing tests green)

**Interfaces:**
- Consumes: existing `AggregateRates`, `InterfaceStats`, `HealthEvent`, `AlertEvent`
- Produces:
  - `LOCAL_HOST_ID: str = "local"` in `monitor.models`
  - `MetricsDatabase.insert_rates(sample, *, host_id: str = LOCAL_HOST_ID)`
  - `MetricsDatabase.insert_interface_snapshots(timestamp, interfaces, *, host_id: str = LOCAL_HOST_ID)`
  - `MetricsDatabase.insert_health_event(event)` — uses `event.host_id`
  - `MetricsDatabase.insert_alert_event(event)` — uses `event.host_id`
  - `MetricsDatabase.get_rate_history(interface, *, minutes, resolution="auto", host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.get_latest_rates(*, host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.get_latest_interface_rates(*, host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.get_latest_interface_snapshots(*, host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.get_health_events(*, limit=50, host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.get_alert_events(*, limit=50, host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.get_overview(*, minutes=5, resolution="auto", host_id=LOCAL_HOST_ID)`
  - `MetricsDatabase.list_hosts(*, online_after_seconds: float = 30.0) -> list[dict]`
  - Migration runs inside `init_schema()` / `_migrate_host_id()`

- [ ] **Step 1: Write the failing migration + isolation tests**

Create `tests/test_storage_hosts.py`:

```python
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from monitor.models import (
    AGGREGATE_INTERFACE,
    LOCAL_HOST_ID,
    AggregateRates,
    HealthEvent,
    InterfaceRates,
)
from monitor.storage import MetricsDatabase


LEGACY_SCHEMA = """
CREATE TABLE rate_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL
);
CREATE TABLE rate_samples_minute (
    bucket_start REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, interface)
);
CREATE TABLE rate_samples_hourly (
    bucket_start REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, interface)
);
CREATE TABLE rate_samples_daily (
    bucket_start REAL NOT NULL,
    interface TEXT NOT NULL,
    recv_bps REAL NOT NULL,
    sent_bps REAL NOT NULL,
    recv_pps REAL NOT NULL,
    sent_pps REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (bucket_start, interface)
);
CREATE TABLE interface_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    name TEXT NOT NULL,
    is_up INTEGER NOT NULL,
    speed_mbps INTEGER NOT NULL,
    duplex TEXT NOT NULL,
    mtu INTEGER NOT NULL,
    bytes_recv INTEGER NOT NULL,
    bytes_sent INTEGER NOT NULL,
    packets_recv INTEGER NOT NULL,
    packets_sent INTEGER NOT NULL,
    errin INTEGER NOT NULL,
    errout INTEGER NOT NULL,
    dropin INTEGER NOT NULL,
    dropout INTEGER NOT NULL
);
CREATE TABLE health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    interface TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);
CREATE TABLE alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    rule_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    interface TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL,
    threshold REAL
);
"""


class HostIdStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "legacy.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _seed_legacy(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            """
            INSERT INTO rate_samples (
                timestamp, interface, recv_bps, sent_bps, recv_pps, sent_pps
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (time.time(), AGGREGATE_INTERFACE, 100.0, 50.0, 0.0, 0.0),
        )
        conn.commit()
        conn.close()

    def test_migrates_legacy_rows_to_local(self) -> None:
        self._seed_legacy()
        db = MetricsDatabase(self.path)
        try:
            latest = db.get_latest_rates(host_id=LOCAL_HOST_ID)
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest["recv_bps"], 100.0)
            cols = {
                row[1]
                for row in db._conn.execute("PRAGMA table_info(rate_samples)").fetchall()
            }
            self.assertIn("host_id", cols)
        finally:
            db.close()

    def test_host_isolation(self) -> None:
        db = MetricsDatabase(Path(self.tempdir.name) / "multi.db")
        try:
            now = time.time()
            local = AggregateRates(
                timestamp=now,
                recv_bps=10.0,
                sent_bps=1.0,
                interfaces=(
                    InterfaceRates("eth0", now, 10.0, 1.0, 1.0, 1.0),
                ),
            )
            remote = AggregateRates(
                timestamp=now,
                recv_bps=99.0,
                sent_bps=9.0,
                interfaces=(
                    InterfaceRates("en0", now, 99.0, 9.0, 1.0, 1.0),
                ),
            )
            db.insert_rates(local, host_id=LOCAL_HOST_ID)
            db.insert_rates(remote, host_id="laptop")
            self.assertEqual(db.get_latest_rates(host_id=LOCAL_HOST_ID)["recv_bps"], 10.0)
            self.assertEqual(db.get_latest_rates(host_id="laptop")["recv_bps"], 99.0)
            hosts = {item["host_id"] for item in db.list_hosts()}
            self.assertEqual(hosts, {LOCAL_HOST_ID, "laptop"})
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_storage_hosts -v`

Expected: FAIL — `LOCAL_HOST_ID` missing and/or `host_id` kwargs / `list_hosts` missing

- [ ] **Step 3: Add `LOCAL_HOST_ID` and `host_id` on events**

In `monitor/models.py`, after `AGGREGATE_INTERFACE`:

```python
LOCAL_HOST_ID = "local"
```

Update `HealthEvent` and `AlertEvent` to include:

```python
host_id: str = LOCAL_HOST_ID
```

(place `host_id` after required fields / with other defaults so existing positional call sites in tests keep working — prefer keyword-only default at end).

- [ ] **Step 4: Update SCHEMA and migration in `storage.py`**

1. Import `LOCAL_HOST_ID` from `monitor.models`.
2. Add `host_id TEXT NOT NULL` to every table in `SCHEMA`, with rollup PKs:

```sql
PRIMARY KEY (bucket_start, host_id, interface)
```

3. Add indexes that include `host_id` (e.g. `ON rate_samples(host_id, interface, timestamp)`).
4. Replace `init_schema` with:

```python
def init_schema(self) -> None:
    with self._lock:
        self._conn.executescript(SCHEMA)
        self._migrate_host_id_locked()
        self._conn.commit()

def _table_columns(self, table: str) -> set[str]:
    rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}

def _migrate_host_id_locked(self) -> None:
    if "host_id" in self._table_columns("rate_samples"):
        return
    # Add host_id to append-only tables
    for table in (
        "rate_samples",
        "interface_snapshots",
        "health_events",
        "alert_events",
    ):
        self._conn.execute(
            f"ALTER TABLE {table} ADD COLUMN host_id TEXT NOT NULL DEFAULT '{LOCAL_HOST_ID}'"
        )
    # Rebuild rollup tables with new PK (copy → drop → recreate → restore)
    for table in (
        "rate_samples_minute",
        "rate_samples_hourly",
        "rate_samples_daily",
    ):
        self._rebuild_rollup_with_host_id_locked(table)

def _rebuild_rollup_with_host_id_locked(self, table: str) -> None:
    staging = f"{table}_host_migrate"
    self._conn.execute(f"DROP TABLE IF EXISTS {staging}")
    # Create staging with new schema (copy DDL from SCHEMA for that table, rename)
    # Copy: INSERT INTO staging SELECT bucket_start, '{LOCAL_HOST_ID}', interface, ...
    # DROP old; RENAME staging → table; recreate indexes
```

Implement `_rebuild_rollup_with_host_id_locked` fully using the new DDL from `SCHEMA` (create-as new name, copy with literal `LOCAL_HOST_ID`, drop old, rename). Fresh DBs created via `SCHEMA` already have `host_id`; migration no-ops when column exists.

5. Update every insert to include `host_id` (default `LOCAL_HOST_ID`).
6. Update every SELECT with `AND host_id = ?` (parameterized).
7. Update rollup SQL `GROUP BY` / `ON CONFLICT` to include `host_id`:

```sql
GROUP BY bucket_start, host_id, interface
ON CONFLICT(bucket_start, host_id, interface) DO UPDATE SET ...
```

8. Add `list_hosts`:

```python
def list_hosts(self, *, online_after_seconds: float = 30.0) -> list[dict[str, Any]]:
    cutoff = time.time() - online_after_seconds
    with self._lock:
        rows = self._conn.execute(
            """
            SELECT host_id, MAX(timestamp) AS last_seen
            FROM rate_samples
            WHERE interface = ?
            GROUP BY host_id
            ORDER BY host_id ASC
            """,
            (AGGREGATE_INTERFACE,),
        ).fetchall()
    return [
        {
            "host_id": row["host_id"],
            "last_seen": row["last_seen"],
            "online": row["last_seen"] >= cutoff,
        }
        for row in rows
    ]
```

- [ ] **Step 5: Run host + existing storage tests**

Run:

```bash
python -m unittest tests.test_storage_hosts tests.test_storage -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add monitor/models.py monitor/storage.py tests/test_storage_hosts.py tests/test_storage.py
git commit -m "$(cat <<'EOF'
feat: namespace metrics storage by host_id with migration

Add LOCAL_HOST_ID and migrate legacy SQLite rows so Phase 4
multi-host agents can share one database.
EOF
)"
```

---

### Task 2: Config + tag local sampler + read API `?host=`

**Files:**
- Modify: `monitor/config.py`
- Modify: `monitor/service.py`
- Modify: `monitor/server.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_server.py` (or create host-scoped cases)

**Interfaces:**
- Consumes: storage APIs from Task 1
- Produces:
  - `AgentsConfig(token: str | None = None)`
  - `ServerConfig.host_id: str = LOCAL_HOST_ID` (import constant or default `"local"`)
  - `AppConfig.agents: AgentsConfig`
  - `SamplingService(..., host_id: str = LOCAL_HOST_ID)` — tags inserts + WS payload `host_id`
  - `create_app(..., host_id: str = LOCAL_HOST_ID, agent_token: str | None = None)`
  - REST handlers accept `host: str = LOCAL_HOST_ID`
  - `GET /api/hosts` → `{"hosts": database.list_hosts()}`

- [ ] **Step 1: Write failing config + API tests**

Add to `tests/test_config.py`:

```python
def test_parse_agents_and_host_id(self) -> None:
    config = parse_config_data(
        {
            "server": {"host_id": "hub-pi"},
            "agents": {"token": "secret"},
        }
    )
    self.assertEqual(config.server.host_id, "hub-pi")
    self.assertEqual(config.agents.token, "secret")
```

Add to `tests/test_server.py` (follow existing FastAPI TestClient patterns in that file):

```python
def test_overview_filters_by_host(self) -> None:
    # insert two hosts via database, GET /api/overview?host=laptop
    # assert recv_bps matches laptop only

def test_list_hosts(self) -> None:
    # GET /api/hosts → includes seeded host_ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_config.ConfigParsingTests.test_parse_agents_and_host_id tests.test_server -v`

Expected: FAIL on missing `agents` / `host_id` / `/api/hosts`

- [ ] **Step 3: Extend config**

```python
@dataclass(frozen=True)
class AgentsConfig:
    token: str | None = None

@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    db: str = "monitor.db"
    host_id: str = "local"

@dataclass(frozen=True)
class AppConfig:
    # ... existing fields ...
    agents: AgentsConfig = field(default_factory=AgentsConfig)
```

In `parse_config_data`:

```python
agents = _section(data, "agents")
# server=ServerConfig(..., host_id=str(server.get("host_id", "local")))
# agents=AgentsConfig(token=_optional_str(agents.get("token")))
```

Also resolve env in server factory later: `os.environ.get("MONITOR_AGENT_TOKEN")` wins over config when set (document in Task 6).

- [ ] **Step 4: Tag `SamplingService`**

Add `host_id: str = LOCAL_HOST_ID` to `__init__`, store as `self.host_id`.

In `_handle_sample`:

```python
self.database.insert_rates(sample, host_id=self.host_id)
self.database.insert_interface_snapshots(
    sample.timestamp, interfaces, host_id=self.host_id
)
# When inserting health/alerts, use dataclasses.replace or construct with host_id=self.host_id
```

Update `_rate_history` to pass `host_id=self.host_id`.

WS payload:

```python
payload = {
    "type": "sample",
    "host_id": self.host_id,
    # ... existing keys ...
}
```

- [ ] **Step 5: Wire `create_app` + query params**

```python
def create_app(..., host_id: str = LOCAL_HOST_ID, agent_token: str | None = None, ...):
    # resolve host_id from app_config.server.host_id when provided
    # resolve agent_token from env MONITOR_AGENT_TOKEN or app_config.agents.token
    service = SamplingService(..., host_id=host_id)
    app.state.agent_token = agent_token
    app.state.host_id = host_id

@app.get("/api/hosts")
async def hosts() -> dict[str, Any]:
    return {"hosts": database.list_hosts()}

@app.get("/api/overview")
async def overview(minutes: float = 5, resolution: Resolution = "auto", host: str = LOCAL_HOST_ID):
    return database.get_overview(minutes=minutes, resolution=resolution, host_id=host)
# same host= param for history, interfaces, health, alerts
```

Update CLI `serve` path to pass `host_id` from config into `create_app`.

- [ ] **Step 6: Run tests**

Run:

```bash
python -m unittest tests.test_config tests.test_server tests.test_storage_hosts -v
```

Expected: PASS (fix any existing server tests that assume no `host` param — defaults keep them working)

- [ ] **Step 7: Commit**

```bash
git add monitor/config.py monitor/service.py monitor/server.py monitor/cli.py tests/test_config.py tests/test_server.py
git commit -m "$(cat <<'EOF'
feat: scope dashboard APIs and local sampler by host_id

Add agents/token and server.host_id config, /api/hosts, and
?host= filters on existing read endpoints.
EOF
)"
```

---

### Task 3: Ingest endpoint + shared-token auth

**Files:**
- Create: `monitor/ingest.py`
- Modify: `monitor/server.py`
- Create: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `MetricsDatabase`, `AlertEngine`, `SamplingService`-like persistence path, `WebSocketBridge.publish`
- Produces:
  - `parse_agent_sample(body: dict) -> tuple[str, AggregateRates, list[InterfaceStats]]`
  - `ingest_agent_sample(database, *, host_id, sample, snapshots, alert_engine, notifiers, on_sample, health_monitor) -> None`
  - `POST /api/agents/samples` → `200 {"ok": true}`

- [ ] **Step 1: Write failing ingest tests**

```python
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from monitor.models import LOCAL_HOST_ID
from monitor.server import create_app


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "ingest.db")
        self.app = create_app(db_path=self.db_path, agent_token="secret", interval=60.0)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.tempdir.cleanup()

    def _payload(self, host_id: str = "laptop") -> dict:
        now = time.time()
        return {
            "host_id": host_id,
            "timestamp": now,
            "recv_bps": 1000.0,
            "sent_bps": 200.0,
            "interfaces": [
                {
                    "name": "en0",
                    "timestamp": now,
                    "recv_bps": 1000.0,
                    "sent_bps": 200.0,
                    "recv_pps": 1.0,
                    "sent_pps": 1.0,
                }
            ],
            "snapshots": [],
        }

    def test_rejects_missing_token(self) -> None:
        response = self.client.post("/api/agents/samples", json=self._payload())
        self.assertEqual(response.status_code, 401)

    def test_rejects_when_token_not_configured(self) -> None:
        app = create_app(db_path=self.db_path + ".notoken", agent_token=None)
        with TestClient(app) as client:
            response = client.post(
                "/api/agents/samples",
                headers={"Authorization": "Bearer secret"},
                json=self._payload(),
            )
        self.assertEqual(response.status_code, 503)

    def test_accepts_valid_sample(self) -> None:
        response = self.client.post(
            "/api/agents/samples",
            headers={"Authorization": "Bearer secret"},
            json=self._payload("laptop"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        overview = self.client.get("/api/overview?host=laptop").json()
        self.assertEqual(overview["latest"]["recv_bps"], 1000.0)

    def test_rejects_empty_host_id(self) -> None:
        body = self._payload()
        body["host_id"] = "  "
        response = self.client.post(
            "/api/agents/samples",
            headers={"Authorization": "Bearer secret"},
            json=body,
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
```

Note: `create_app` starts `SamplingService` — use a large `interval` and stop service in lifespan if tests flake; follow existing `test_server.py` patterns for shutting down.

- [ ] **Step 2: Run to verify fail**

Run: `python -m unittest tests.test_ingest -v`

Expected: FAIL (404 on `/api/agents/samples`)

- [ ] **Step 3: Implement `monitor/ingest.py`**

```python
"""Validate and persist samples posted by remote agents."""

from __future__ import annotations

from typing import Any

from monitor.models import (
    AggregateRates,
    InterfaceRates,
    InterfaceStats,
)


class IngestError(ValueError):
    """Invalid agent payload."""


def parse_agent_sample(
    body: dict[str, Any],
) -> tuple[str, AggregateRates, list[InterfaceStats]]:
    host_id = str(body.get("host_id", "")).strip()
    if not host_id:
        raise IngestError("host_id is required")
    try:
        timestamp = float(body["timestamp"])
        recv_bps = float(body["recv_bps"])
        sent_bps = float(body["sent_bps"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestError("timestamp, recv_bps, and sent_bps are required numbers") from exc

    interfaces_raw = body.get("interfaces") or []
    if not isinstance(interfaces_raw, list):
        raise IngestError("interfaces must be a list")
    interfaces: list[InterfaceRates] = []
    for item in interfaces_raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
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
    sample = AggregateRates(
        timestamp=timestamp,
        recv_bps=recv_bps,
        sent_bps=sent_bps,
        interfaces=tuple(interfaces),
    )
    snapshots: list[InterfaceStats] = []
    for item in body.get("snapshots") or []:
        if not isinstance(item, dict) or "name" not in item:
            continue
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
    return host_id, sample, snapshots
```

Add `persist_agent_sample(...)` that mirrors `SamplingService._handle_sample` for a remote host: insert rates/snapshots with `host_id`, run `HealthMonitor.evaluate` if snapshots present (per-host monitor instance can live on `app.state` as a `dict[str, HealthMonitor]` or a single monitor keyed by host — **use `dict[str, HealthMonitor]` on app/service helper**), run `alert_engine.evaluate` with `history_getter` bound to that `host_id`, insert alerts with `host_id`, call `on_sample` with `host_id` in payload.

Simplest approach that matches the spec: add `SamplingService.ingest_remote(...)` method that reuses alert/health/notifier paths with an explicit `host_id` argument, and call it from the route. Prefer this over duplicating logic in `ingest.py` — keep `ingest.py` for parsing only; persistence on the service.

```python
# monitor/service.py
def ingest_remote(
    self,
    host_id: str,
    sample: AggregateRates,
    snapshots: list[InterfaceStats],
) -> None:
    self.database.insert_rates(sample, host_id=host_id)
    if snapshots:
        self.database.insert_interface_snapshots(
            sample.timestamp, snapshots, host_id=host_id
        )
    monitor = self._health_for_host(host_id)
    events = monitor.evaluate(sample.timestamp, snapshots) if snapshots else []
    for event in events:
        self.database.insert_health_event(
            HealthEvent(..., host_id=host_id)  # copy fields from event
        )
    # alerts + on_sample with host_id — same structure as _handle_sample
```

- [ ] **Step 4: Add route**

```python
from fastapi import Header, HTTPException
from monitor.ingest import IngestError, parse_agent_sample

@app.post("/api/agents/samples")
async def agent_samples(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    token = app.state.agent_token
    if not token:
        raise HTTPException(status_code=503, detail="Agent ingest is not configured")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Invalid or missing agent token")
    try:
        host_id, sample, snapshots = parse_agent_sample(body)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        service.ingest_remote(host_id, sample, snapshots)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to persist sample") from exc
    return {"ok": True}
```

- [ ] **Step 5: Run ingest + server tests**

Run: `python -m unittest tests.test_ingest tests.test_server -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add monitor/ingest.py monitor/service.py monitor/server.py tests/test_ingest.py
git commit -m "$(cat <<'EOF'
feat: accept authenticated multi-host agent sample ingest

Add POST /api/agents/samples with shared bearer token auth and
host-scoped persistence through the sampling service.
EOF
)"
```

---

### Task 4: `monitor agent` CLI client

**Files:**
- Create: `monitor/agent_client.py`
- Modify: `monitor/cli.py`
- Create: `tests/test_agent_client.py`
- Modify: `tests/test_config.py` if CLI default wiring needs coverage

**Interfaces:**
- Consumes: `BandwidthCollector`, `list_interface_stats`, httpx
- Produces:
  - `resolve_agent_host_id(explicit: str | None) -> str`
  - `build_agent_payload(host_id, sample, snapshots) -> dict`
  - `run_agent(*, server, token, host_id, interval, ...) -> int` exit code
  - CLI: `python -m monitor agent --server URL --token TOKEN`

- [ ] **Step 1: Write failing client tests**

```python
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from monitor.agent_client import build_agent_payload, resolve_agent_host_id
from monitor.models import AggregateRates, InterfaceRates, InterfaceStats


class AgentClientTests(unittest.TestCase):
    def test_resolve_host_id_explicit(self) -> None:
        self.assertEqual(resolve_agent_host_id("kitchen-pi"), "kitchen-pi")

    @patch("monitor.agent_client.socket.gethostname", return_value="Andy-MacBook.local")
    def test_resolve_host_id_hostname(self, _mock: MagicMock) -> None:
        self.assertEqual(resolve_agent_host_id(None), "Andy-MacBook.local")

    def test_build_payload(self) -> None:
        now = 1000.0
        sample = AggregateRates(
            timestamp=now,
            recv_bps=1.0,
            sent_bps=2.0,
            interfaces=(InterfaceRates("en0", now, 1.0, 2.0, 0.0, 0.0),),
        )
        payload = build_agent_payload(
            "laptop",
            sample,
            [
                InterfaceStats(
                    name="en0",
                    is_up=True,
                    speed_mbps=1000,
                    duplex="full",
                    mtu=1500,
                    bytes_recv=1,
                    bytes_sent=2,
                    packets_recv=1,
                    packets_sent=1,
                    errin=0,
                    errout=0,
                    dropin=0,
                    dropout=0,
                )
            ],
        )
        self.assertEqual(payload["host_id"], "laptop")
        self.assertEqual(payload["recv_bps"], 1.0)
        self.assertEqual(payload["interfaces"][0]["name"], "en0")
        self.assertEqual(payload["snapshots"][0]["name"], "en0")
```

Add a test that `post_sample` calls httpx with `Authorization: Bearer secret` (mock `httpx.Client.post`).

- [ ] **Step 2: Run to verify fail**

Run: `python -m unittest tests.test_agent_client -v`

Expected: FAIL — module missing

- [ ] **Step 3: Implement `monitor/agent_client.py`**

```python
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
```

- [ ] **Step 4: Wire CLI `agent` subcommand**

In `build_parser`, add:

```python
agent_parser = subparsers.add_parser(
    "agent",
    help="Sample local interfaces and post rates to a central hub.",
)
agent_parser.add_argument("--server", required=True, help="Hub base URL, e.g. http://192.168.1.10:8080")
agent_parser.add_argument(
    "--token",
    default=None,
    help="Shared agent token (default: MONITOR_AGENT_TOKEN env).",
)
agent_parser.add_argument("--host-id", default=None, help="Override host id (default: hostname).")
agent_parser.add_argument("--interval", type=float, default=None)
agent_parser.add_argument("--history-size", type=int, default=None)
agent_parser.add_argument("--duration", type=float, default=None)
agent_parser.add_argument("--samples", type=int, default=None)
_add_interface_filters(agent_parser)
```

In `apply_config_defaults`, handle `agent` like `watch` for interval/history/filters.

In `main`:

```python
elif args.command == "agent":
    import os
    from monitor.agent_client import resolve_agent_host_id, run_agent
    token = args.token or os.environ.get("MONITOR_AGENT_TOKEN")
    if not token:
        print("Agent token required via --token or MONITOR_AGENT_TOKEN", file=sys.stderr)
        return 2
    host_id = resolve_agent_host_id(args.host_id)
    return run_agent(
        server=args.server,
        token=token,
        host_id=host_id,
        interval=args.interval,
        history_size=args.history_size,
        include=include or None,
        exclude=exclude or None,
        duration=args.duration,
        samples=args.samples,
    )
```

- [ ] **Step 5: Run tests**

Run: `python -m unittest tests.test_agent_client tests.test_ingest -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add monitor/agent_client.py monitor/cli.py tests/test_agent_client.py
git commit -m "$(cat <<'EOF'
feat: add monitor agent CLI to post samples to the hub

Agents reuse the local collector and POST authenticated payloads
to POST /api/agents/samples on an interval.
EOF
)"
```

---

### Task 5: Dashboard host selector + WS filter

**Files:**
- Modify: `monitor/static/index.html`
- Modify: `monitor/static/app.js`
- Modify: `monitor/static/styles.css`
- Optional: `tests/test_server.py` contract for `/api/hosts` shape already covered — add a short static-file smoke if the suite already does so; otherwise manual check listed below

**Interfaces:**
- Consumes: `GET /api/hosts`, existing APIs with `?host=`
- Produces: UI state `selectedHost`; all fetches include host; WS ignores other hosts

- [ ] **Step 1: Add host control to HTML**

In the header or Live Overview `panel-header`, add:

```html
<label class="host-picker">
  Host
  <select id="host-select"></select>
</label>
```

- [ ] **Step 2: Update `app.js` state and fetches**

```javascript
const state = {
  selectedHost: "local",
  selectedInterface: null,
  selectedMinutes: 5,
  hosts: [],
  interfaces: [],
  overviewPoints: [],
  socket: null,
};

const hostSelectEl = document.getElementById("host-select");

function hostQuery() {
  return `host=${encodeURIComponent(state.selectedHost)}`;
}

async function refreshHosts() {
  const response = await fetch("/api/hosts");
  const data = await response.json();
  state.hosts = data.hosts || [];
  hostSelectEl.innerHTML = "";
  for (const host of state.hosts) {
    const option = document.createElement("option");
    option.value = host.host_id;
    const badge = host.online ? "" : " (offline)";
    option.textContent = `${host.host_id}${badge}`;
    hostSelectEl.appendChild(option);
  }
  if (!state.hosts.some((h) => h.host_id === state.selectedHost)) {
    const local = state.hosts.find((h) => h.host_id === "local");
    state.selectedHost = local ? local.host_id : (state.hosts[0]?.host_id || "local");
  }
  hostSelectEl.value = state.selectedHost;
}

hostSelectEl.addEventListener("change", async () => {
  state.selectedHost = hostSelectEl.value;
  state.overviewPoints = [];
  state.selectedInterface = null;
  await refreshDashboard();
});
```

Update every `fetch("/api/overview...")`, history, interfaces, health to append `&${hostQuery()}` or `?${hostQuery()}`.

In WS `onmessage`:

```javascript
if (message.host_id && message.host_id !== state.selectedHost) {
  return;
}
```

Call `refreshHosts()` during initial load (and optionally every 30s).

- [ ] **Step 3: Minimal CSS for host picker**

Align with existing `.controls label` styles — reuse classes; only add spacing if needed:

```css
.host-picker {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
}
```

- [ ] **Step 4: Manual verification**

Run hub:

```bash
MONITOR_AGENT_TOKEN=devsecret python -m monitor serve --host 127.0.0.1 --port 8080
```

In another shell:

```bash
MONITOR_AGENT_TOKEN=devsecret python -m monitor agent --server http://127.0.0.1:8080 --host-id fake-laptop --samples 3
```

Open `http://127.0.0.1:8080/`, confirm host dropdown lists `local` and `fake-laptop`, and charts switch.

- [ ] **Step 5: Commit**

```bash
git add monitor/static/index.html monitor/static/app.js monitor/static/styles.css
git commit -m "$(cat <<'EOF'
feat: add dashboard host selector for multi-host metrics

Filter REST and WebSocket updates by selected host_id so agents
appear alongside the local sampler.
EOF
)"
```

---

### Task 6: Docs, example config, README Phase 4 status

**Files:**
- Modify: `config.example.yaml`
- Modify: `README.md` (roadmap status + agent section under Phase 4)
- Modify: `docs/superpowers/specs/2026-07-18-phase4-multi-host-agents-design.md` status → Implemented (optional)

**Interfaces:**
- Consumes: completed behavior from Tasks 1–5
- Produces: operator docs for hub token, bind advice, agent command

- [ ] **Step 1: Update `config.example.yaml`**

```yaml
server:
  host: 127.0.0.1
  port: 8080
  db: monitor.db
  # host_id for this machine's local sampler (default: local)
  # host_id: local

agents:
  # Shared bearer token for POST /api/agents/samples
  # Prefer MONITOR_AGENT_TOKEN env in production.
  token: null
```

- [ ] **Step 2: Update README**

1. Roadmap table: Phase 4 → **Done** — Multi-host agents (per-device NIC rates → central hub)
2. Replace/expand “Phase 4 — Home LAN…” planned section with implementation notes:
   - Hub: set `agents.token` or `MONITOR_AGENT_TOKEN`; bind `--host 0.0.0.0` on LAN
   - Agent: `python -m monitor agent --server http://HUB:8080`
   - Dashboard host selector
   - Security: read APIs still open; do not expose publicly without a reverse proxy
   - Explicit note: Eero/router monitoring remains a separate future option (see deferred Eero spec)
3. Keep Phase 4 approach table but mark **Agent on each device** as implemented; others still future

- [ ] **Step 3: Run full test suite**

Run: `python -m unittest discover -s tests -v`

Expected: PASS (0 failures)

- [ ] **Step 4: Commit**

```bash
git add config.example.yaml README.md docs/superpowers/specs/2026-07-18-phase4-multi-host-agents-design.md
git commit -m "$(cat <<'EOF'
docs: document Phase 4 multi-host agent setup

Mark Phase 4 done in the roadmap and document hub token config,
agent CLI usage, and LAN bind guidance.
EOF
)"
```

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| G1 agent CLI posts samples | 4 |
| G2 shared bearer token | 3, 4, 6 |
| G3 `host_id` namespacing | 1 |
| G4 migrate to `"local"` | 1 |
| G5 dashboard host selector | 5 |
| G6 local sampler is a host + shared alerts | 2, 3 |
| `GET /api/hosts` | 2 |
| Read APIs `?host=` | 2 |
| WS includes `host_id` | 2, 5 |
| Ingest errors 401/400/503 | 3 |
| Config `agents.token` / `server.host_id` | 2, 6 |
| README + example config | 6 |
| Eero deferred | 6 (docs note) |
| Alerts on ingested samples | 3 (`ingest_remote`) |

## Self-review notes

- No TBD/placeholder steps remain; open UI offline threshold fixed as `list_hosts(online_after_seconds=30)` + “(offline)” label
- Ingest success status fixed as `200 {"ok": true}`
- Rollup migration strategy fixed as rebuild-with-copy in Task 1
- Type names consistent: `LOCAL_HOST_ID`, `parse_agent_sample`, `ingest_remote`, `run_agent`
