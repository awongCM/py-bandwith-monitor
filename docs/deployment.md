# Deployment

Run the dashboard on an always-on host so history survives reboots and you can
open the UI from any device on your LAN.

**Config:** copy `config.example.yaml` → `config.yaml`. CLI flags override YAML.

**Data:** SQLite path is `--db` / `server.db`. Use a persistent volume or
directory — ephemeral containers lose the database on restart.

**LAN access:** bind `0.0.0.0` and open `http://<host-ip>:8080`. Do not expose
port 8080 publicly without auth in front.

See also [Secrets](secrets.md).

## Docker (Linux host networking)

Bridge networking shows **container** NICs to `psutil`, not the host's
`eth0`/`wlan0`. On Linux use `--network host`. On macOS/Windows Docker, run the
venv on the host instead.

```bash
cp config.example.yaml config.yaml
docker build -t bandwidth-monitor .
docker run -d \
  --name bandwidth-monitor \
  --restart unless-stopped \
  --network host \
  -v bandwidth-monitor-data:/data \
  -v "$(pwd)/config.yaml:/data/config.yaml:ro" \
  bandwidth-monitor
```

Or `docker compose up -d --build` (expects `./config.yaml` beside
`docker-compose.yml`).

Optional webhook: `-e ALERT_WEBHOOK_URL=https://hooks.example.com/alert`

## systemd (bare metal)

```bash
sudo mkdir -p /opt/bandwidth-monitor /var/lib/bandwidth-monitor /etc/bandwidth-monitor
sudo git clone https://github.com/andywongcheeming/py-bandwith-monitor.git /opt/bandwidth-monitor
cd /opt/bandwidth-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock || .venv/bin/pip install -r requirements.txt
sudo cp config.example.yaml /etc/bandwidth-monitor/config.yaml
sudo edit /etc/bandwidth-monitor/config.yaml
sudo cp deploy/systemd/bandwidth-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bandwidth-monitor.service
```

Logs: `journalctl -u bandwidth-monitor.service -f`

## Always-on Mac

```bash
cp config.example.yaml ~/bandwidth-monitor/config.yaml
python -m monitor serve --config ~/bandwidth-monitor/config.yaml \
  --host 0.0.0.0 --port 8080 --db ~/bandwidth-monitor/monitor.db
```

Prevent sleep (Energy Saver) or use `caffeinate` / `tmux`.

## Render (optional)

Bind `0.0.0.0:$PORT` and attach a persistent disk for SQLite:

```bash
python -m monitor serve --config /data/config.yaml --host 0.0.0.0 --port $PORT --db /data/monitor.db
```

## Multi-host agents

Hub — set a shared token and bind on the LAN:

```bash
export MONITOR_AGENT_TOKEN="replace-with-a-long-random-secret"
python -m monitor serve --host 0.0.0.0 --port 8080
```

Agent — on each remote host:

```bash
export MONITOR_AGENT_TOKEN="replace-with-a-long-random-secret"
python -m monitor agent --server http://HUB:8080
# optional: --host-id my-laptop
```

Ingest requires the bearer token; read APIs and the dashboard are open. Do not
expose the hub publicly without a reverse proxy and auth.
