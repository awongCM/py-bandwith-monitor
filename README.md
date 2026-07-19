# py-bandwidth-monitor

Local network bandwidth monitor in Python. Reads kernel counters via `psutil`,
shows live upload/download rates per interface, and optionally serves a web
dashboard with SQLite history, alerts, and multi-host agents.

Optional sibling **[Eero household monitor](docs/eero-monitor.md)** reports
per-device rates from an Eero mesh via the unofficial cloud API.

## Requirements

- Python 3.10+ (`monitor`); Python 3.12+ for optional `eero_monitor`
- See `requirements.txt` (main) and `requirements-eero.txt` (Eero)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt    # or requirements.lock for pinned deps
```

**Secrets:** this is a public repo. Copy `config.example.yaml` → `config.yaml`
and `.env.example` → `.env` locally; never commit them. Details:
[docs/secrets.md](docs/secrets.md).

## Quick start

```bash
python -m monitor snapshot          # one-shot interface stats
python -m monitor watch             # live rates in the terminal
python -m monitor serve             # dashboard → http://127.0.0.1:8080
```

Legacy entry point: `python main.py snapshot|watch|serve`.

**macOS:** interfaces are usually `en0` (Wi‑Fi), not `eth0`:

```bash
python -m monitor serve --include en0
```

Stop long-running commands with **Ctrl+C** (not Cmd+C on Mac).

## Commands

| Command | Description |
|---------|-------------|
| `snapshot` | Link status and cumulative byte/packet counters |
| `watch` | Live per-interface upload/download rates |
| `serve` | FastAPI dashboard + background sampler |
| `agent` | POST local NIC rates to a central hub |

Common flags: `--include` / `--exclude` (interface globs), `--json`, `--config
config.yaml`, `--host`, `--port`, `--db`.

```bash
python -m monitor watch --interval 2 --duration 30
python -m monitor serve --host 0.0.0.0 --port 8080 --db monitor.db
python -m monitor agent --server http://HUB:8080 --token "$MONITOR_AGENT_TOKEN"
```

Config file sections match `config.example.yaml` (`interfaces`, `sampling`,
`server`, `agents`, `retention`, `thresholds`, `notifications`). CLI flags
override YAML.

## Dashboard

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) after `serve`. Features:
host selector (hub + agents), live overview sparklines, per-interface charts (5 /
15 / 60 min), interface table, health events, threshold alerts.

Raw samples default to 7 days in `monitor.db`; rollups keep longer history.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/hosts` | Known hosts and last-seen |
| `GET /api/overview?minutes=5&host=local` | Totals + aggregate history |
| `GET /api/history?interface=eth0&minutes=15&host=local` | Per-interface history |
| `GET /api/interfaces?host=local` | Latest snapshots and rates |
| `GET /api/health?limit=50&host=local` | Health events |
| `POST /api/agents/samples` | Agent ingest (Bearer token) |
| `WS /ws/live` | Live sample stream |

## Eero household monitor (optional)

Separate app for **household devices** via the Eero cloud API (not local NICs).

```bash
python3.12 -m venv .venv-eero && source .venv-eero/bin/activate
pip install -r requirements-eero.txt
python -m eero_monitor login --user you@example.com   # once → .env
set -a && source .env && set +a
python -m eero_monitor serve    # http://127.0.0.1:8081
```

Private family access (Cloudflare Tunnel + Access), Windows always-on host, and
full setup: **[docs/eero-monitor.md](docs/eero-monitor.md)**.

## Deployment

Always-on home server, Docker, systemd, agents, Render:
**[docs/deployment.md](docs/deployment.md)**.

## Project structure

```
monitor/           # main bandwidth monitor (CLI, dashboard, agents)
eero_monitor/      # optional Eero household monitor (isolated)
deploy/
  systemd/         # Linux unit file
  windows/         # serve.ps1 for Task Scheduler
tests/             # unit + integration tests
docs/              # deployment, Eero, secrets guides
config.example.yaml
requirements.txt
requirements-eero.txt
```

Design notes and phase history: `docs/superpowers/specs/` and `docs/superpowers/plans/`.

## License

MIT — see [LICENSE](LICENSE).
