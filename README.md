# py-bandwidth-monitor

Simple local network interface bandwidth monitor written in Python.

This tool reads kernel network counters on the machine where it runs using
`psutil`. It is useful for checking interface status, cumulative traffic, and
live upload/download rates per network interface.

Phase 2 adds a web dashboard with SQLite history, REST APIs, and WebSocket live
updates. Phase 3 adds YAML config, retention rollups, threshold alerts with
webhook notifications, and Docker/systemd deployment packaging.

## Requirements

- Python 3.10+
- `psutil`
- `fastapi`
- `uvicorn`
- `pyyaml` (optional config file)

## Install

```bash
pip install -r requirements.txt
```

For reproducible installs, use the pinned lockfile:

```bash
pip install -r requirements.lock
```

Recommended: use a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

## Usage

Take a one-shot snapshot of monitored interfaces:

```bash
python -m monitor snapshot
```

Watch live upload and download rates in the terminal:

```bash
python -m monitor watch
```

Start the web dashboard:

```bash
python -m monitor serve
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in your browser.

Run an agent that posts samples to a central hub (Phase 4):

```bash
python -m monitor agent --server http://HUB:8080 --token "$MONITOR_AGENT_TOKEN"
```

The legacy entry point still works:

```bash
python main.py snapshot
python main.py watch
python main.py serve
```

### Running on macOS

On a MacBook, interface names are usually `en0` (Wi‑Fi), not Linux-style `eth0`
/ `wlan0`. Check what's available first:

```bash
python3 -m monitor snapshot
```

Then monitor a specific interface:

```bash
python3 -m monitor serve --include en0
python3 -m monitor watch --include en0
```

Stop terminal commands with **Ctrl+C** (not Cmd+C — Cmd+C is copy on Mac).

### Dashboard

The dashboard includes:

- **Host selector** — switch between the hub (`local`) and reporting agents
- **Live overview** — total upload/download speeds with sparklines
- **Per interface** — interface selector and 5 / 15 / 60 minute charts
- **Interface table** — link status, cumulative totals, errors, and drops
- **Health panel** — link up/down events and rising error/drop alerts
- **Alert status** — header indicator for armed thresholds; recent alerts via `/api/alerts`

Sample data is stored locally in SQLite (`monitor.db` by default). Raw 1s
samples are retained for 7 days by default; minute/hourly/daily rollups keep
longer history for charts.

```bash
python -m monitor serve --host 0.0.0.0 --port 8080 --db monitor.db --interval 1
```

### Configuration file

Copy `config.example.yaml` to `config.yaml` (or pass `--config /path/to/config.yaml`).
The file is optional; defaults match the CLI when no config is present.

| Section | Keys | Purpose |
|---------|------|---------|
| `interfaces` | `include`, `exclude` | Glob patterns for monitored NICs |
| `sampling` | `interval`, `history_size` | Sample interval and in-memory buffer |
| `server` | `host`, `port`, `db`, `host_id` | Dashboard bind, SQLite path, local sampler id |
| `agents` | `token` | Shared bearer token for agent ingest (`MONITOR_AGENT_TOKEN` preferred) |
| `retention` | `days`, `minute_samples_days`, … | Raw + rollup retention windows |
| `thresholds` | `recv_bps`, `sent_bps`, `total_bps`, … | Alert engine thresholds |
| `notifications` | `webhook_url` | Optional alert webhook (`ALERT_WEBHOOK_URL` overrides) |

CLI flags override config values. Example for a LAN-accessible home host:

```bash
cp config.example.yaml config.yaml
# edit interfaces.include for your NIC (e.g. en0)
python -m monitor serve
```

### Interface filters

By default, loopback and common virtual interfaces are excluded (`lo`,
`tun*`, `utun*`, `docker*`, `veth*`, `br-*`, `virbr*`, `wg*`, `vmnet*`, and
similar tunnel/container patterns).

Monitor only specific interfaces:

```bash
python -m monitor watch --include eth0 --include wlan0
python -m monitor serve --include en0
```

Exclude additional interfaces:

```bash
python -m monitor snapshot --exclude docker0 --exclude br-*
```

### JSON output

```bash
python -m monitor snapshot --json
python -m monitor watch --json
```

### Watch options

```bash
python -m monitor watch --interval 2 --history-size 1800
python -m monitor watch --duration 30
python -m monitor watch --samples 10
```

`watch` keeps recent samples in an in-memory ring buffer. The default history
size is 3600 samples, which is about one hour at a 1 second interval.

### Stopping long-running commands

On a normal terminal, press **Ctrl+C** (not Cmd+C on Mac) to stop.

Cloud agent and web terminals often do not forward keyboard interrupts reliably.
Use one of these instead:

```bash
python -m monitor watch --duration 30
python -m monitor watch --samples 10
pkill -f "monitor watch"
pkill -f "monitor serve"
```

## Commands

| Command | Description |
|---------|-------------|
| `snapshot` | Print interface link status and cumulative byte/packet counters |
| `watch` | Print live per-interface upload/download rates every interval |
| `serve` | Start the FastAPI dashboard and background sampler |
| `agent` | Sample local NICs and POST rates to a central hub |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/hosts` | Known hosts and last-seen timestamps |
| `GET /api/overview?minutes=5&host=local` | Latest totals plus aggregate history |
| `GET /api/history?interface=eth0&minutes=15&host=local` | Per-interface rate history |
| `GET /api/interfaces?host=local` | Latest interface snapshots and rates |
| `GET /api/health?limit=50&host=local` | Recent health events |
| `POST /api/agents/samples` | Agent ingest (Bearer token required) |
| `WS /ws/live` | Live sample stream for the dashboard (`host_id` in hello/samples) |

---

## Project scope

| Monitors | Does not monitor (yet) |
|----------|------------------------|
| NICs on the hub and agent hosts | Devices without a running agent (phones/TVs unless an agent runs there) |
| Cumulative kernel counters since boot | Per-process or per-connection usage |
| Aggregate or per-interface upload/download rates | Router QoS, ISP usage caps, WAN-only traffic |
| Local interface up/down and link speed | Router/Eero household views (deferred; separate spec) |

**Original intent:** a small home utility to inspect bandwidth on the local
machine — interface metadata, cumulative I/O, and live transfer rates.

**Phase 4 adds:** lightweight agents that report the same NIC rates to a central
hub. Router APIs, SNMP, packet capture, and Eero cloud monitoring remain out of
scope for now.

---

## Roadmap

| Phase | Status | Summary |
|-------|--------|---------|
| **Phase 1** | Done | Collector refactor, CLI (`snapshot`, `watch`), per-interface rates |
| **Phase 2** | Done | SQLite storage, FastAPI server, Chart.js dashboard |
| **Phase 3** | Done | Config, retention rollups, alerts/webhook, Docker/systemd, integration tests |
| **Phase 4** | Done | Multi-host agents (per-device NIC rates → central hub) |

---

## Phase design and implementation

### Phase 1 — Collector and CLI

**Goal:** Turn the original prototype into a structured, testable monitoring
tool with proper CLI commands.

**Deliverables**

- Refactor monolithic `main.py` into a `monitor/` package
- `snapshot` command — one-shot interface status and cumulative counters
- `watch` command — live per-interface upload/download rates (separate, not combined)
- Interface filtering (`--include` / `--exclude`) with defaults for loopback and virtual NICs
- In-memory ring buffer during `watch`
- `--json` output for scripting
- `requirements.txt` with pinned `psutil`
- Unit tests for filtering, formatting, and collector sampling

**Architecture**

```mermaid
flowchart LR
    CLI[monitor/cli.py] --> Collector[monitor/collector.py]
    Collector --> Psutil[psutil net_if_stats / net_io_counters]
    Collector --> Models[monitor/models.py]
    CLI --> Formatting[monitor/formatting.py]
```

**Key files**

| File | Role |
|------|------|
| `monitor/collector.py` | Sampling, rate calculation, ring buffer |
| `monitor/cli.py` | `snapshot` and `watch` commands |
| `monitor/models.py` | `InterfaceStats`, `InterfaceRates`, `AggregateRates` |
| `monitor/formatting.py` | Human-readable bytes and bit rates |

---

### Phase 2 — Web dashboard

**Goal:** Visual dashboard with persistent history so monitoring is not limited
to terminal output.

**Deliverables**

- SQLite time-series storage (`monitor/storage.py`)
- Background sampler thread (`monitor/service.py`)
- Health event detection — link up/down, rising errors/drops (`monitor/health.py`)
- FastAPI REST + WebSocket server (`monitor/server.py`)
- `serve` CLI command
- Chart.js dashboard UI (`monitor/static/`)

**Dashboard views**

1. **Live overview** — total up/down speed, sparklines
2. **Per interface** — toggle interface (e.g. `en0` / `eth0`), line chart over 5 / 15 / 60 min
3. **Interface table** — link status, cumulative totals, error/drop counts
4. **Health indicators** — link down events, rising error rates

**Tech stack (lightweight, Python-native)**

| Layer | Choice |
|-------|--------|
| Backend | FastAPI — REST + WebSocket for live updates |
| Storage | SQLite — local history with minute/hourly/daily rollups |
| Frontend | Plain HTML + CSS + Chart.js (no React build step) |
| Sampling | Background thread via `psutil` |

**Architecture**

```mermaid
flowchart LR
    subgraph collector [Background collector]
        Sampler[SamplingService thread]
        Health[HealthMonitor]
    end

    Psutil[psutil] --> Sampler
    Sampler --> SQLite[(monitor.db)]
    Sampler --> Health
    Health --> SQLite

    subgraph api [FastAPI server]
        REST[REST /api/*]
        WS[WebSocket /ws/live]
    end

    SQLite --> REST
    Sampler --> WS
    REST --> UI[Chart.js dashboard]
    WS --> UI
```

**SQLite schema**

| Table | Purpose |
|-------|---------|
| `rate_samples` | Per-interface and aggregate transfer rates over time (raw 1s) |
| `rate_samples_minute` / `_hourly` / `_daily` | Rollup averages for longer history windows |
| `interface_snapshots` | Link status, MTU, cumulative bytes/packets/errors/drops |
| `health_events` | Link up/down, high error/drop alerts |
| `alert_events` | Threshold alert firings (bandwidth, sustained errors) |

**Key files**

| File | Role |
|------|------|
| `monitor/storage.py` | SQLite read/write, retention pruning |
| `monitor/service.py` | Background sampler + WebSocket bridge |
| `monitor/health.py` | Link and error/drop event detection |
| `monitor/server.py` | FastAPI app, routes, static file serving |
| `monitor/static/index.html` | Dashboard layout |
| `monitor/static/app.js` | Charts, WebSocket client, API polling |
| `monitor/static/styles.css` | Dashboard styling |

**Development split (git worktrees)**

Phase 2 was implemented in parallel worktrees and merged:

| Worktree branch | Responsibility |
|-----------------|----------------|
| `cursor/phase2-storage-api-3189` | SQLite, health checks, FastAPI server, `serve` CLI |
| `cursor/phase2-frontend-3189` | Chart.js dashboard UI |
| `cursor/phase2-dashboard-3189` | Integration branch |

```bash
# Example worktree setup for future phases
git worktree add -b cursor/phase3-alerts-3189 ../worktrees/phase3-alerts master
git worktree add -b cursor/phase3-ui-3189 ../worktrees/phase3-ui master
```

---

### Phase 3 — Alerts, rollups, and deployment (done)

**Goal:** Production-ready home monitoring on an always-on machine (Mac,
Raspberry Pi, NAS).

**Deliverable status**

| Area | Status | Notes |
|------|--------|-------|
| **Config file** | Done | `monitor/config.py`, `config.example.yaml`, CLI `--config` |
| **Polish** | Done | Virtual-interface filtering improvements, `requirements.lock` |
| **Retention rollups** | Done | Minute/hourly/daily tables + scheduled maintenance |
| **Alerts** | Done | Threshold engine (`monitor/alerts.py`) + dashboard alerts panel |
| **Notifications** | Done (webhook) | Webhook notifier; email/desktop stubs reserved for later |
| **Deployment** | Done | `Dockerfile`, `docker-compose.yml`, `deploy/systemd/bandwidth-monitor.service` |
| **Testing** | Done | Config, retention, alerts, and `tests/test_integration.py` |

**Key files**

| File | Role |
|------|------|
| `monitor/config.py` | YAML config load/merge with CLI defaults |
| `monitor/retention.py` | Retention settings and maintenance helpers |
| `monitor/alerts.py` / `alerts_settings.py` | Threshold evaluation and settings |
| `monitor/notifiers.py` | Webhook delivery |
| `Dockerfile` / `docker-compose.yml` | Container packaging |
| `deploy/systemd/bandwidth-monitor.service` | Bare-metal unit file |

---

## Deployment (home server / Raspberry Pi)

Run the dashboard on an always-on host so history survives reboots and you can
open the UI from any device on your LAN.

**Config:** copy `config.example.yaml` → `config.yaml`, then pass
`--config /path/to/config.yaml` (or rely on `./config.yaml` in the working
directory). CLI flags override YAML.

**Data persistence:** SQLite lives at the path passed to `--db` / `server.db`.
Mount a volume or dedicated directory — the database is lost if the container
filesystem is ephemeral.

**LAN access:** bind to `0.0.0.0` (Docker and systemd examples below do this via
CLI flags that override `server.host`). Open `http://<host-ip>:8080` from
another machine on the network. Do not expose port 8080 to the public internet
without a reverse proxy and authentication.

### Docker (Linux host networking recommended for real NICs)

Bridge networking shows **container** interfaces to `psutil`, not the host’s
`eth0`/`wlan0`. On Linux, use host networking (or prefer systemd below) so the
monitor sees the same NICs as the host. On macOS/Windows Docker, host network
mode is limited — run via venv/systemd/`launchd` on the host instead.

```bash
cp config.example.yaml config.yaml
# edit interfaces.include (eth0 / en0), retention, notifications.webhook_url

docker build -t bandwidth-monitor .
docker run -d \
  --name bandwidth-monitor \
  --restart unless-stopped \
  --network host \
  -v bandwidth-monitor-data:/data \
  -v "$(pwd)/config.yaml:/data/config.yaml:ro" \
  bandwidth-monitor
```

Or use Compose (expects `./config.yaml` beside `docker-compose.yml`; default
compose file uses `network_mode: host` on Linux):

```bash
cp config.example.yaml config.yaml
docker compose up -d --build
```

The image runs as a non-root `monitor` user, installs from `requirements.lock`
when present (else `requirements.txt`), and defaults to
`--config /data/config.yaml --host 0.0.0.0 --port 8080 --db /data/monitor.db`.
A missing config file falls back to built-in defaults. With `--network host`,
port publish (`-p`) is unnecessary — open `http://<host-ip>:8080` directly.

Optional webhook override without editing YAML:

```bash
docker run -d ... -e ALERT_WEBHOOK_URL=https://hooks.example.com/alert bandwidth-monitor
```

### systemd (bare metal / venv install)

1. Clone the repo, install deps, and install a config file:

```bash
sudo mkdir -p /opt/bandwidth-monitor /var/lib/bandwidth-monitor /etc/bandwidth-monitor
sudo git clone https://github.com/andywongcheeming/py-bandwith-monitor.git /opt/bandwidth-monitor
cd /opt/bandwidth-monitor
python3 -m venv .venv
# Prefer the pinned lockfile when available:
.venv/bin/pip install -r requirements.lock || .venv/bin/pip install -r requirements.txt
sudo cp config.example.yaml /etc/bandwidth-monitor/config.yaml
sudo edit /etc/bandwidth-monitor/config.yaml   # interfaces, retention, webhook_url
sudo useradd --system --home /opt/bandwidth-monitor --shell /usr/sbin/nologin monitor || true
sudo chown -R monitor:monitor /opt/bandwidth-monitor /var/lib/bandwidth-monitor
sudo chown root:monitor /etc/bandwidth-monitor/config.yaml
sudo chmod 640 /etc/bandwidth-monitor/config.yaml
```

2. Install the unit file and start the service:

```bash
sudo cp deploy/systemd/bandwidth-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bandwidth-monitor.service
sudo systemctl status bandwidth-monitor.service
```

The unit runs `serve --config /etc/bandwidth-monitor/config.yaml` with
`--host 0.0.0.0` and `--db /var/lib/bandwidth-monitor/monitor.db` (CLI overrides
YAML). Optional: set `Environment=ALERT_WEBHOOK_URL=...` in the unit.

Logs: `journalctl -u bandwidth-monitor.service -f`

### Always-on Mac

Use Docker as above, or run under `launchd` with the same `python -m monitor serve`
command. For a quick LAN-visible instance without Docker:

```bash
cp config.example.yaml ~/bandwidth-monitor/config.yaml
python -m monitor serve --config ~/bandwidth-monitor/config.yaml \
  --host 0.0.0.0 --port 8080 --db ~/bandwidth-monitor/monitor.db
```

Keep the Mac awake (Energy Saver → prevent sleep when display is off, or use
`caffeinate` in a `tmux`/`screen` session).

### Render (optional cloud host)

If you deploy to [Render](https://render.com), bind to `0.0.0.0:$PORT` and
attach a persistent disk for SQLite — Render's filesystem is ephemeral without
one. Mount or bake `config.yaml`, then:

```bash
python -m monitor serve --config /data/config.yaml --host 0.0.0.0 --port $PORT --db /data/monitor.db
```

Set `ALERT_WEBHOOK_URL` as a Render secret if you use webhook notifications.

---

### Phase 4 — Multi-host agents

**Goal:** Collect per-device NIC rates from other machines and show them on one
central hub dashboard.

**Implemented**

- Shared bearer token on `POST /api/agents/samples` (`agents.token` or
  `MONITOR_AGENT_TOKEN`)
- `python -m monitor agent` samples local interfaces and posts to the hub
- Metrics namespaced by `host_id` (hub local sampler defaults to `local`)
- Dashboard host selector; charts/interfaces scoped to the selected host
- Alerts and retention apply to both local and ingested samples

**Hub setup**

Set a shared token in `config.yaml` (or prefer `MONITOR_AGENT_TOKEN` in
production). Bind on the LAN so agents can reach the hub:

```yaml
agents:
  token: "replace-with-a-long-random-secret"
```

```bash
export MONITOR_AGENT_TOKEN="replace-with-a-long-random-secret"
python -m monitor serve --host 0.0.0.0 --port 8080
```

**Agent setup** (on each remote Python host)

```bash
export MONITOR_AGENT_TOKEN="replace-with-a-long-random-secret"
python -m monitor agent --server http://HUB:8080
# optional: --host-id my-laptop  (default: machine hostname)
```

**Dashboard:** use the host selector to switch between the hub (`local`) and
reporting agents. Hosts with no samples for ~30s are labeled offline.

**Security:** ingest requires the shared token; read APIs and the dashboard are
still open. Do not expose the hub publicly without a reverse proxy (and ideally
auth) in front. Anyone with the shared token can post under any `host_id`
(including `local`), so treat the token like a household secret.

**Still future / deferred:** router APIs, SNMP, mirror-port collectors, and
Eero/router household monitoring remain separate options — see
`docs/superpowers/specs/2026-07-18-eero-monitor-design.md` (deferred).

**Approach options**

| Approach | Effort | Status | What you get |
|----------|--------|--------|--------------|
| Agent on each device | Medium | **Implemented** | Accurate per-machine stats, reports to central dashboard |
| Router API (UniFi, OpenWrt, pfSense) | Medium | Future | Per-device traffic if the router exposes it |
| SNMP from router | Medium | Future | WAN/LAN totals, sometimes per-port |
| Mirror port + flow collector (ntopng) | High | Future | Full LAN visibility |

---

## Project structure

```
monitor/
  cli.py           # snapshot, watch, serve, agent commands
  config.py        # YAML startup config loader
  collector.py     # psutil sampling and rate calculation
  agent_client.py  # remote agent loop (POST samples to hub)
  ingest.py        # validate agent payloads
  retention.py     # rollup retention settings
  alerts.py        # threshold alert engine
  alerts_settings.py
  notifiers.py     # webhook notifications
  storage.py       # SQLite persistence + rollups (host_id scoped)
  service.py       # background sampler + remote ingest
  health.py        # link/error health events
  server.py        # FastAPI app
  models.py        # data types
  formatting.py    # human-readable output helpers
  static/
    index.html     # dashboard page
    app.js         # Chart.js + WebSocket client
    styles.css     # dashboard styles
tests/
  test_monitor.py
  test_config.py
  test_retention.py
  test_alerts.py
  test_storage.py
  test_storage_hosts.py
  test_server.py
  test_ingest.py
  test_agent_client.py
  test_integration.py
deploy/
  systemd/
    bandwidth-monitor.service
Dockerfile
docker-compose.yml
main.py            # legacy entry point
config.example.yaml
config.yaml        # local override (gitignored; not committed)
requirements.txt
requirements.lock  # pinned transitive deps
monitor.db         # created at runtime (gitignored)
```


---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
