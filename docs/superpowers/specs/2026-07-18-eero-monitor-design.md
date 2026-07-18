# Eero Household Monitor — Design Spec

**Date:** 2026-07-18  
**Status:** Approved for implementation planning  
**Repo:** `py-bandwidth-monitor`  
**Related app:** existing `monitor` (local host interface bandwidth)

## 1. Problem

The existing `monitor` package reads kernel counters via `psutil` on the machine where it runs. That is correct for **this host’s** interfaces (`en0`, tunnels, etc.), but it cannot see per-device traffic for other clients on a home mesh.

The home network uses an **Amazon Eero 6**. Eero is closed (no SSH, SNMP, or port mirror). Household device lists and instantaneous per-device usage are available through Eero’s **cloud API**, reachable via unofficial community SDKs.

We need a household-device monitor that:

- Lists all known devices (online and offline)
- Shows live / historical per-device and aggregate rates
- Exposes CLI + web dashboard (parity with `monitor serve`)
- Does **not** couple into `monitor`, so replacing the router later does not force a rewrite of host interface monitoring

## 2. Goals

| ID | Goal |
|----|------|
| G1 | Sibling app `eero_monitor` with CLI (`devices`, `watch`, `serve`) and FastAPI dashboard |
| G2 | SQLite history (default 7-day retention) + WebSocket live updates |
| G3 | Depend on an unofficial Eero Python SDK, wrapped behind a thin client |
| G4 | Auth via environment variables only (`EERO_SESSION`, `EERO_NETWORK_ID`) |
| G5 | Track **all known** devices; offline or missing rates are stored as `0.0` bps |
| G6 | Zero imports from `monitor` into `eero_monitor` (and vice versa) |
| G7 | Document that this app is optional and Eero-specific |

## 3. Non-goals (v1)

- Interactive `login` command, keyring, or OAuth UI
- Merging host `monitor` and Eero dashboards into one UI
- ARP scanning or packet capture for per-device bytes
- Device pause / block / priority controls via Eero
- Official Amazon Eero Data Portability partner API
- Guaranteeing packet-capture accuracy (Eero reports cloud instantaneous usage)

## 4. Decisions

| Topic | Choice | Rationale |
|-------|--------|-----------|
| Delivery | CLI + web dashboard | Match user mental model of `monitor` |
| History | SQLite + WS live | Same persistence pattern as Phase 2 `monitor` |
| Eero access | Unofficial library (e.g. `fulviofreitas/eero-api`) | Faster than hand-rolling REST; wrap so SDK is swappable |
| Package boundary | Fully isolated | Router swap = delete/stop `eero_monitor` only |
| Auth | Env vars only | Simple, headless-friendly; no secrets in git |
| Device set | All known (online + offline) | Presence + usage in one table |
| Architecture | Mirror `monitor` 1:1 | Familiar structure; predictable implementation plan |

## 5. Architecture

```text
py-bandwidth-monitor/
├── monitor/                    # UNCHANGED — local host interfaces
├── eero_monitor/               # NEW — household devices via Eero
│   ├── __init__.py
│   ├── __main__.py
│   ├── auth.py                 # read EERO_* env; fail clearly if missing
│   ├── client.py               # thin wrapper over unofficial Eero SDK
│   ├── models.py               # device-centric dataclasses
│   ├── collector.py            # poll → normalize → AggregateDeviceRates
│   ├── formatting.py           # local rate/byte helpers (no shared package)
│   ├── storage.py              # SQLite (default eero_monitor.db)
│   ├── health.py               # online/offline (+ API/auth) events
│   ├── service.py              # background sampler + WS bridge
│   ├── server.py               # FastAPI REST + WebSocket + static UI
│   ├── cli.py                  # devices | watch | serve
│   └── static/                 # index.html, app.js, styles.css
├── requirements.txt            # monitor deps only
├── requirements-eero.txt       # eero SDK + fastapi/uvicorn/httpx as needed
├── tests/
│   ├── test_*.py               # existing monitor tests
│   └── eero_monitor/           # isolated eero_monitor tests
└── docs/superpowers/specs/     # this design + later plans
```

### Hard rules

1. `eero_monitor` **must not** import `monitor`.
2. Separate database file: default `eero_monitor.db`.
3. Separate default bind: `127.0.0.1:8081` (host `monitor` stays on `8080`).
4. Eero SDK types must not leak past `client.py`; collector/CLI/server use only our models.
5. Secrets only via environment; never commit session tokens.

### Runtime data flow (`serve`)

```text
Eero cloud API
    → unofficial SDK
    → client.py (map to DeviceSnapshot / rates)
    → collector (interval default 5s)
    → SQLite + health events
    → FastAPI REST + /ws/live
    → dashboard
```

CLI `devices` = one fetch, no persist.  
CLI `watch` = collector loop printing samples (optional `--json`, `--duration`, `--samples`).

## 6. Components

### 6.1 Auth (`auth.py`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `EERO_SESSION` | Yes | Session cookie/token usable by the SDK |
| `EERO_NETWORK_ID` | Yes | Target Eero network ID |

Behavior: if either is missing/empty, exit non-zero with a message naming both variables and pointing at README setup. No interactive prompts in v1.

How users obtain values is documented in README (manual one-time login via SDK example or documented community flow). Token refresh is out of scope for v1; expired session surfaces as auth errors (see §8).

### 6.2 Client (`client.py`)

Responsibilities:

- Construct SDK client from env credentials
- `list_devices() -> list[DeviceSnapshot]` for all known devices
- Extract instantaneous down/up when present; otherwise treat rates as unknown → `0.0` at collector boundary
- Defensive mapping: missing optional fields become `None` / defaults; one bad device must not fail the whole list

Pin the concrete package in `requirements-eero.txt` at implementation time (preferred candidate: `eero` / `fulviofreitas/eero-api` or the installable name confirmed during planning). If the package cannot support Amazon-only login accounts, README documents the known workaround (secondary admin with email/password).

### 6.3 Models (`models.py`)

Device-centric dataclasses (frozen, `to_dict()` like `monitor`):

**`DeviceSnapshot`**

- `device_id: str`
- `name: str` (nickname or fallback hostname/MAC)
- `mac: str | None`
- `ip: str | None`
- `is_online: bool`
- `connection: str` (`wifi` | `wired` | `unknown`)
- `signal: float | None` (optional)
- `last_seen: float | None` (optional epoch seconds)

**`DeviceRates`**

- `device_id: str`
- `name: str`
- `timestamp: float`
- `recv_bps: float`
- `sent_bps: float`
- `is_online: bool`

**`AggregateDeviceRates`**

- `timestamp: float`
- `recv_bps: float` (sum of device recv)
- `sent_bps: float` (sum of device sent)
- `devices: tuple[DeviceRates, ...]`

**`HealthEvent`**

- `timestamp: float`
- `device_id: str` (use `"__api__"` for non-device API/auth events)
- `event_type: str` (`online`, `offline`, `auth_error`, `api_error`, …)
- `severity: str`
- `message: str`
- `value: float | None = None`

Aggregate sentinel for history queries (analogous to `monitor`’s `__total__`): `__total__`.

### 6.4 Collector (`collector.py`)

- Default poll interval: **5.0** seconds (cloud API; not 1s like local `psutil`)
- Each tick: list devices → build `DeviceRates` (0 bps when offline or rate missing) → `AggregateDeviceRates`
- Rate semantics: store **router-reported instantaneous** usage from Eero each poll. Document clearly that this is not kernel-counter delta math.

### 6.5 Storage (`storage.py`)

Mirror `monitor` storage responsibilities with device IDs:

- Persist rate samples (aggregate + per-device)
- Persist latest device snapshots
- Persist health events
- Query overview, history by `device_id` or `__total__`, latest devices, recent health
- Retention purge (default **7** days), invoked periodically from the sampling service

Exact schema is an implementation detail but must support the API in §7 without joining to `monitor.db`.

### 6.6 Health (`health.py`)

- Compare previous vs current online flags → emit `online` / `offline` events
- Surface auth/API failures as health events for the dashboard panel

### 6.7 Service & server

Same pattern as `monitor`:

- `SamplingService` background thread/loop writing SQLite and publishing samples
- `WebSocketBridge` for live dashboard updates
- FastAPI lifespan starts/stops the service

### 6.8 CLI (`cli.py`)

| Command | Description |
|---------|-------------|
| `devices` | One-shot device list; `--json` supported |
| `watch` | Live rates; `--interval`, `--json`, `--duration`, `--samples` |
| `serve` | Dashboard; `--host` (default `127.0.0.1`), `--port` (default `8081`), `--db` (default `eero_monitor.db`), `--interval` (default `5`), `--history-size`, `--retention-days` (default `7`) |

Entry: `python -m eero_monitor …`

### 6.9 Dashboard UI

Parity with host dashboard, device-oriented:

- Live aggregate up/down + sparkline
- Device table: name, IP, MAC, online badge, connection, current rates
- Device selector + 5 / 15 / 60 minute history charts
- Health panel for online/offline and API/auth events
- Clear labeling that data is **household (via Eero)**, not this machine’s NICs

Visual style may follow the existing dashboard for consistency within the monorepo, but assets live under `eero_monitor/static/` (no shared static package required in v1).

## 7. HTTP API

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML |
| `GET /api/overview?minutes=5` | Latest aggregate totals + history |
| `GET /api/history?device=__total__&minutes=15` | Aggregate or per-device rate history |
| `GET /api/devices` | Latest snapshots + latest rates |
| `GET /api/health?limit=50` | Recent health events |
| `WS /ws/live` | Live sample stream |

## 8. Error handling

| Failure | Behavior |
|---------|----------|
| Missing `EERO_SESSION` or `EERO_NETWORK_ID` | Fail before sampling; non-zero exit; message lists required vars |
| Auth / HTTP 401 | Health event `auth_error`; CLI exits; `serve` retries next interval and shows degraded state in UI |
| Timeout / network error | Health event `api_error`; skip sample; retry next interval |
| Unexpected SDK / JSON shape | Map defensively; skip bad device entries; do not crash sampler |
| Empty device list | Valid empty aggregate; UI shows empty state |
| Single DB write failure | Log + health event; continue process |

## 9. Dependencies

- `requirements.txt` — unchanged; host `monitor` only
- `requirements-eero.txt` — unofficial Eero SDK + FastAPI + uvicorn (+ httpx if not pulled in transitively)

Install path documented as optional:

```bash
pip install -r requirements-eero.txt
export EERO_SESSION=...
export EERO_NETWORK_ID=...
python -m eero_monitor serve
```

## 10. Testing

No live Eero calls in CI. Mock `client.py` or the SDK behind it.

| Area | Coverage |
|------|----------|
| `auth` | Missing/partial env → clear failure |
| `client` mapping | Fixture JSON → snapshots/rates; offline → 0 bps; optional fields absent |
| `collector` | Aggregates; online+offline mix; empty list |
| `health` | online↔offline transitions |
| `storage` | Insert, history by device, retention |
| `server` | REST shapes with seeded DB / fake service |
| Boundary | Assert `eero_monitor` modules do not import `monitor` |

## 11. Documentation updates (implementation)

When implementing (not part of this spec commit unless desired later):

- README section **“Optional: Eero household monitor”** — purpose, install, env vars, commands, port 8081, unofficial-API disclaimer, Amazon-login caveat if applicable
- Explicit: replacing Eero → remove/stop `eero_monitor`; keep `monitor`

## 12. Success criteria (v1)

1. `python -m eero_monitor devices` lists all known devices with online status  
2. `watch` and the dashboard show per-device and aggregate rates from Eero  
3. SQLite retains configurable history (default 7 days); 5/15/60 minute charts work  
4. No import coupling between `eero_monitor` and `monitor`  
5. Missing credentials fail clearly without partial startup  

## 13. Implementation phasing (hint for plan)

Suggested order for the later implementation plan (not executed by this spec):

1. Package skeleton + auth + models + mocked client  
2. Collector + CLI `devices` / `watch`  
3. Storage + health + service  
4. FastAPI + WebSocket + dashboard  
5. README + `requirements-eero.txt` + isolation test  

## 14. Open implementation details

These are intentionally deferred to the implementation plan / first PR, not blockers for the design:

- Exact PyPI package name and version pin for the unofficial SDK  
- Precise SQLite table DDL  
- Exact JSON field paths inside SDK responses (confirmed against live fixtures during implementation)  
- Dashboard CSS polish level (functional parity first)
