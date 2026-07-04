# py-bandwidth-monitor

Simple local network interface bandwidth monitor written in Python.

This tool reads kernel network counters on the machine where it runs using
`psutil`. It is useful for checking interface status, cumulative traffic, and
live upload/download rates per network interface.

Phase 2 adds a web dashboard with SQLite history, REST APIs, and WebSocket live
updates.

## Requirements

- Python 3.10+
- `psutil`
- `fastapi`
- `uvicorn`

## Install

```bash
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

The legacy entry point still works:

```bash
python main.py snapshot
python main.py watch
python main.py serve
```

### Dashboard

The dashboard includes:

- **Live overview** — total upload/download speeds with sparklines
- **Per interface** — interface selector and 5 / 15 / 60 minute charts
- **Interface table** — link status, cumulative totals, errors, and drops
- **Health panel** — link up/down events and rising error/drop alerts

Sample data is stored locally in SQLite (`monitor.db` by default) and retained
for 7 days.

```bash
python -m monitor serve --host 0.0.0.0 --port 8080 --db monitor.db --interval 1
```

### Interface filters

By default, loopback and common virtual interfaces are excluded (`lo`,
`docker*`, `veth*`, `br-*`, `virbr*`).

Monitor only specific interfaces:

```bash
python -m monitor watch --include eth0 --include wlan0
python -m monitor serve --include eth0 --include wlan0
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

### Stopping `watch`

On a normal terminal, press **Ctrl+C** (not Cmd+C on Mac) to stop.

Cloud agent and web terminals often do not forward keyboard interrupts reliably.
Use one of these instead:

```bash
python -m monitor watch --duration 30
python -m monitor watch --samples 10
tmux -f /exec-daemon/tmux.portal.conf kill-session -t bandwidth-watch
pkill -f "monitor watch"
pkill -f "monitor serve"
```

## Commands

| Command | Description |
|---------|-------------|
| `snapshot` | Print interface link status and cumulative byte/packet counters |
| `watch` | Print live per-interface upload/download rates every interval |
| `serve` | Start the FastAPI dashboard and background sampler |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/overview?minutes=5` | Latest totals plus aggregate history |
| `GET /api/history?interface=eth0&minutes=15` | Per-interface rate history |
| `GET /api/interfaces` | Latest interface snapshots and rates |
| `GET /api/health?limit=50` | Recent health events |
| `WS /ws/live` | Live sample stream for the dashboard |

## Development with worktrees

Phase 2 was split across git worktrees:

- `cursor/phase2-storage-api-3189` — SQLite, health checks, FastAPI server
- `cursor/phase2-frontend-3189` — Chart.js dashboard UI

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
