# py-bandwidth-monitor

Simple local network interface bandwidth monitor written in Python.

This tool reads kernel network counters on the machine where it runs using
`psutil`. It is useful for checking interface status, cumulative traffic, and
live upload/download rates per network interface.

## Requirements

- Python 3.10+
- `psutil`

## Install

```bash
pip install -r requirements.txt
```

## Usage

Take a one-shot snapshot of monitored interfaces:

```bash
python -m monitor snapshot
```

Watch live upload and download rates:

```bash
python -m monitor watch
```

The legacy entry point still works:

```bash
python main.py snapshot
python main.py watch
```

### Interface filters

By default, loopback and common virtual interfaces are excluded (`lo`,
`docker*`, `veth*`, `br-*`, `virbr*`).

Monitor only specific interfaces:

```bash
python -m monitor watch --include eth0 --include wlan0
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
```

`watch` keeps recent samples in an in-memory ring buffer. The default history
size is 3600 samples, which is about one hour at a 1 second interval.

## Commands

| Command | Description |
|---------|-------------|
| `snapshot` | Print interface link status and cumulative byte/packet counters |
| `watch` | Print live per-interface upload/download rates every interval |

## Next steps

- Phase 2: web dashboard with FastAPI and historical charts
- Optional: router or multi-host monitoring for LAN-wide visibility

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
