# Phase 4 — Multi-Host Agents Design Spec

**Date:** 2026-07-18  
**Status:** Approved for implementation planning  
**Repo:** `py-bandwidth-monitor`  
**Related:** existing `monitor` package (Phases 1–3)  
**Out of scope for this spec:** `docs/superpowers/specs/2026-07-18-eero-monitor-design.md` (deferred)

## 1. Problem

Phases 1–3 measure bandwidth on the machine running `monitor serve` via `psutil` kernel counters. That cannot see other devices on the home LAN (phones, TVs, other laptops) without a new data source.

Phase 4 adds a **lightweight agent** that runs the same collector on other hosts and posts samples to the central FastAPI server. Router APIs, SNMP, packet capture, and Eero cloud monitoring are explicitly deferred.

## 2. Goals

| ID | Goal |
|----|------|
| G1 | `python -m monitor agent` samples local NICs and POSTs aggregate + per-interface rates to the hub |
| G2 | Shared-secret bearer token auth on ingest only |
| G3 | Namespace all persisted metrics by `host_id` in the existing SQLite DB (Approach 1) |
| G4 | Migrate pre-Phase-4 DBs by adding `host_id` and backfilling `"local"` |
| G5 | Extend the current dashboard with a host selector; then existing interface/charts UX |
| G6 | Local hub sampler is itself a host; agents and hub share one retention/alerts pipeline |

## 3. Non-goals (v1)

- Router / SNMP / mirror-port / Eero integrations
- Per-agent tokens or OAuth
- Agent-side SQLite persistence
- TLS termination inside the app (document reverse-proxy if exposing beyond LAN)
- Phone-native agents (Python hosts only: laptops, desktops, Pis)
- Merging host-NIC monitoring with any future router household UI
- Changing alert rule semantics beyond scoping evaluation/storage by `host_id`

## 4. Decisions

| Topic | Choice | Rationale |
|-------|--------|-----------|
| Data source | Agent on each device | Works without router vendor APIs |
| Packaging | Same `monitor` package | Reuse collector; no second sampling stack |
| Auth | Shared bearer token | Simple home-LAN security |
| Payload | Aggregate + per-interface | Matches existing models |
| Storage | `host_id` column on existing tables | One DB, one retention path |
| Hub default `host_id` | `"local"` | Preserves continuity with migrated history |
| Agent default `host_id` | `socket.gethostname()` | Zero-config; override with `--host-id` |
| Dashboard | Host selector on current UI | Smallest UX change |
| Eero design | Deferred | Separate later track |

## 5. Architecture

```text
[Host A]  python -m monitor agent --server http://hub:8080
[Host B]  python -m monitor agent --server http://hub:8080
                │  POST /api/agents/samples
                │  Authorization: Bearer <shared token>
                ▼
[Hub]     python -m monitor serve   (local sampler host_id = "local" by default)
                │
                ▼
         SQLite (host_id on samples / rollups / snapshots / health / alerts)
                │
                ▼
         Dashboard: host selector → interfaces / charts (existing UI)
```

### Hard rules

1. Agents live in the same `monitor` package (`agent` CLI subcommand).
2. Ingest requires a configured shared token; missing token disables ingest (local sampler still runs).
3. Read APIs and the dashboard remain unauthenticated in v1 (same as Phase 2/3). Operators must not expose the hub to the public internet without a reverse proxy / network controls.
4. No imports or coupling to any `eero_monitor` package.
5. Rate semantics stay kernel-counter deltas → bps (unchanged from Phases 1–3).

## 6. Components

### 6.1 Config

Extend YAML / env:

| Key | Env | Purpose |
|-----|-----|---------|
| `agents.token` | `MONITOR_AGENT_TOKEN` | Shared secret; required for ingest |
| `server.host_id` | — | Override hub sampler host id (default `"local"`) |

Agent CLI also accepts `--token` / env `MONITOR_AGENT_TOKEN` and `--server` (required).

### 6.2 CLI: `agent`

```bash
python -m monitor agent \
  --server http://hub:8080 \
  --token "$MONITOR_AGENT_TOKEN" \
  --host-id optional-override \
  --interval 1.0 \
  [--include ...] [--exclude ...] \
  [--config path]
```

Behavior:

- Resolve `host_id`: `--host-id` → else `socket.gethostname()`
- Run the existing collector loop (same include/exclude semantics as `watch` / `serve`)
- Each sample: POST JSON to `{server}/api/agents/samples` with bearer token
- On network/HTTP failure: log and retry next interval (no local DB)
- Support `--duration` / `--samples` for short runs (parity with `watch`)

### 6.3 Ingest API

`POST /api/agents/samples`

Headers: `Authorization: Bearer <token>`

Body (conceptual shape):

```json
{
  "host_id": "laptop.local",
  "timestamp": 1720000000.0,
  "recv_bps": 1234.0,
  "sent_bps": 567.0,
  "interfaces": [
    {
      "name": "en0",
      "timestamp": 1720000000.0,
      "recv_bps": 1200.0,
      "sent_bps": 500.0,
      "recv_pps": 10.0,
      "sent_pps": 5.0
    }
  ],
  "snapshots": []
}
```

- Persist aggregate row under interface `__total__` and each interface row, all tagged with `host_id`
- Optionally persist interface snapshots when provided
- Emit / store health transitions for that host when snapshots allow (same health logic as local, scoped by host)
- Publish a WS live event that includes `host_id` so the UI can filter

Responses:

| Condition | Status |
|-----------|--------|
| Valid token + payload | `200` with `{"ok": true}` |
| Missing/invalid token | `401` |
| Token not configured on hub | `503` with clear message |
| Missing/empty `host_id` or invalid body | `400` |

### 6.4 Read APIs

| Endpoint | Change |
|----------|--------|
| `GET /api/hosts` | **New** — list `{host_id, last_seen, online?}` from recent samples |
| `GET /api/overview` | Optional `?host=` (default `"local"`) |
| `GET /api/history` | Optional `?host=` (default `"local"`) |
| `GET /api/interfaces` | Optional `?host=` (default `"local"`) |
| `GET /api/health` | Optional `?host=` (default `"local"`) |
| `GET /api/alerts` | Optional `?host=` (default `"local"`) |
| `WS /ws/live` | Payloads include `host_id`; UI ignores other hosts |

Default `host=` remains `"local"` so existing bookmarks and single-host installs keep working after migration.

### 6.5 Storage & migration

Add `host_id TEXT NOT NULL` to:

- `rate_samples`
- `rate_samples_minute` / `_hourly` / `_daily`
- `interface_snapshots`
- `health_events`
- `alert_events`

Index / primary-key changes:

- Rollup PKs become `(bucket_start, host_id, interface)`
- History indexes include `host_id`

Migration on DB open:

1. Detect missing `host_id` columns
2. Add columns with default `"local"` (or add + `UPDATE … SET host_id = 'local'`)
3. Rebuild rollup tables / indexes as needed for the new PK shape (implementation may recreate rollup tables if SQLite PK alteration is impractical — acceptable; raw `rate_samples` history must be preserved)

Retention purge and rollup maintenance remain global but operate with `host_id` in grouping keys.

### 6.6 Service / alerts

- Local `SamplingService` tags every insert and WS publish with the hub `host_id` (default `"local"`)
- Alert evaluation runs in the hub process on **every** accepted sample (local sampler and ingested agents) using the same threshold config; events are stored under that sample’s `host_id`. Agents inherit hub threshold/webhook settings; they do not carry their own alert config.

### 6.7 Dashboard

- Add a host dropdown populated from `/api/hosts`
- Default selection: `"local"` if present, else first host
- All existing fetches (`overview`, `history`, `interfaces`, `health`, alerts) pass `host=`
- WS handler updates live charts only when `host_id` matches selection
- No second page; no card-heavy redesign

## 7. Error handling

| Failure | Behavior |
|---------|----------|
| Missing/invalid agent token on ingest | `401`; sample dropped |
| Hub has no token configured | Ingest `503`; local sampler unaffected |
| Agent cannot reach hub | Log + retry next interval |
| Empty/missing `host_id` | `400` |
| Stale agent | Remains in `/api/hosts` with old `last_seen`; UI may show offline badge if `now - last_seen` exceeds threshold (e.g. 3× interval or 30s) |
| Single DB write failure on ingest | `500`; agent retries next tick |

## 8. Testing

No multi-machine CI required.

| Area | Coverage |
|------|----------|
| Migration | Fixture pre-Phase-4 DB → columns exist; rows are `"local"`; history readable |
| Ingest auth | Missing/wrong/right token |
| Ingest persist | Multi-host samples isolated by `host_id` |
| Read APIs | `?host=` filters correctly; default `"local"` |
| Agent client | Mock HTTP; posts expected JSON + header |
| Dashboard contract | Host list + host-scoped API shapes (lightweight) |
| Alerts | Ingested sample can create alert_events tagged with agent `host_id` |

## 9. Documentation updates (implementation)

- README Phase 4 status → Done (or In progress → Done at end)
- New section: multi-host agents — hub token config, bind advice (`0.0.0.0` on LAN), `agent` command examples
- Note: Eero/router path remains a separate future option; this phase does not implement it
- `config.example.yaml` gains `agents.token` / `server.host_id` examples

## 10. Success criteria (v1)

1. An agent on a second machine posts authenticated aggregate + per-interface samples to the hub  
2. Hub stores them under that agent’s `host_id`; migrated history stays under `"local"`  
3. Dashboard host selector switches interfaces and charts per host  
4. Automated tests cover migration, auth, and host-scoped queries  

## 11. Implementation phasing (hint for plan)

1. Schema migration + storage APIs accept/filter `host_id`  
2. Tag local sampler with hub `host_id`; wire read API `?host=`  
3. Ingest endpoint + token config  
4. `agent` CLI client  
5. Dashboard host selector + WS filter  
6. Alerts scoping + README / example config  

## 12. Open implementation details

Deferred to the implementation plan / first PR (not design blockers):

- Exact offline badge threshold in the UI (candidate: `max(30s, 3 × sampling interval)`)
- Precise SQLite migration strategy for rollup PK rebuild (recreate-and-copy vs in-place)
