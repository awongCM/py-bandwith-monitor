# Eero household monitor

Optional sibling package for **household device** lists and per-device rates via
the unofficial Eero cloud API. It does **not** share a database with `monitor`.

**Disclaimer:** unofficial `eero-api` SDK — may break; not affiliated with
Amazon/Eero. Requires **Python 3.12+**.

See [Secrets](secrets.md) before copying credentials.

## Install

```bash
python3.12 -m venv .venv-eero          # macOS: /opt/homebrew/bin/python3.13 -m venv .venv-eero
source .venv-eero/bin/activate         # Windows: .\.venv-eero\Scripts\Activate.ps1
pip install -r requirements-eero.txt
```

## Credentials

One-time login:

```bash
python -m eero_monitor login --user you@example.com
```

Copy the printed exports into `.env` (from `.env.example`), then:

```bash
set -a && source .env && set +a
```

Amazon-only accounts often need a secondary email/password admin in the Eero app.
[Troubleshooting wiki](https://github.com/fulviofreitas/eero-api/wiki/Troubleshooting).

## Commands

```bash
python -m eero_monitor devices      # one-shot device list
python -m eero_monitor watch        # live terminal rates
python -m eero_monitor serve        # dashboard on http://127.0.0.1:8081
```

Defaults: `eero_monitor.db`, 7-day retention, 5s poll interval. First sample after
startup is 0 bps while counters prime.

## Private access (Cloudflare Tunnel + Access)

Expose the dashboard to family phones/laptops without opening router ports.
Requires a **domain on Cloudflare** (own or ~$10–15/yr). Use e.g.
`eero.example.com`. Tailscale is the no-domain alternative.

The app has **no built-in auth** — use Cloudflare Access (email + one-time PIN).

### Prerequisites

- Domain active on Cloudflare
- [Zero Trust](https://one.dash.cloudflare.com/) (free household tier)
- `eero_monitor serve` working locally with `EERO_*` in `.env`
- Always-on host (see [Windows home host](#always-on-home-host-windows-10) below)

### Steps

**1. Run locally (loopback only)**

```bash
source .venv-eero/bin/activate
set -a && source .env && set +a
python -m eero_monitor serve
```

**2. Install cloudflared**

```bash
brew install cloudflared                    # macOS
winget install Cloudflare.cloudflared       # Windows
```

[Other platforms](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)

**3. Log in** (creates `~/.cloudflared/cert.pem` — required before `tunnel create`)

```bash
cloudflared tunnel login    # pick your domain zone in the browser
ls ~/.cloudflared/cert.pem
```

**4. Create tunnel**

```bash
cloudflared tunnel create eero-monitor
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /Users/you/.cloudflared/<TUNNEL-UUID>.json
# Windows: C:\Users\you\.cloudflared\<TUNNEL-UUID>.json

ingress:
  - hostname: eero.example.com
    service: http://127.0.0.1:8081
  - service: http_status:404
```

```bash
cloudflared tunnel route dns eero-monitor eero.example.com
cloudflared tunnel run eero-monitor
```

Or use **Zero Trust → Networks → Tunnels** in the dashboard.

**5. Cloudflare Access**

Zero Trust → **Access → Applications → Self-hosted** → domain `eero.example.com`
→ policy allowing your email(s) → One-time PIN.

**6. Run on boot**

```bash
sudo cloudflared service install && sudo cloudflared service start   # macOS/Linux
```

Keep `eero_monitor serve` running (`launchd`, Task Scheduler, or
`deploy/windows/serve.ps1`).

## Always-on home host (Windows 10)

Use a home Windows PC when your MacBook travels. Same Cloudflare URL; only the
origin host changes.

| Approach | Domain? | Family access |
|----------|---------|---------------|
| Windows + Cloudflare + Access | Yes | Browser + email PIN |
| Tailscale | No | Tailscale app |
| Cloud VPS / Render | Optional | + monthly cost |

**Setup**

```powershell
winget install Python.Python.3.12
git clone https://github.com/andywongcheeming/py-bandwith-monitor.git
cd py-bandwith-monitor
py -3.12 -m venv .venv-eero
.\.venv-eero\Scripts\Activate.ps1
pip install -r requirements-eero.txt
# copy .env from Mac or run login
powershell -ExecutionPolicy Bypass -File deploy\windows\serve.ps1
```

**Move tunnel from Mac**

1. Stop `serve` + `cloudflared` on Mac
2. Copy `~/.cloudflared/` → `C:\Users\<you>\.cloudflared\`
3. Fix `credentials-file` path in `config.yml`
4. `cloudflared tunnel run eero-monitor` on Windows

**Auto-start:** Task Scheduler runs `deploy\windows\serve.ps1`; admin PowerShell:
`cloudflared service install`. Disable Windows sleep on AC power.

## Security

| Topic | Guidance |
|-------|----------|
| Access | Required — do not publish hostname without a policy |
| `EERO_SESSION` | Host-only `.env`; never commit |
| Tunnel creds | `~/.cloudflared/` outside the repo |
| Session expiry | Re-run `login` on the host; update `.env` |

`eero_monitor` can run on a cloud VPS (Eero API, not local NICs) with persistent
disk — keep Access in front. The main `monitor` app should stay on the machine
whose interfaces you measure.
