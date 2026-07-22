# Eero household monitor

Optional sibling package for **household device** lists and per-device rates via
the unofficial Eero cloud API. It does **not** share a database with `monitor`.

**Disclaimer:** unofficial `eero-api` SDK — may break; not affiliated with
Amazon/Eero. Requires **Python 3.12+**.

See [Secrets](secrets.md) before copying credentials.

## Install

```bash
python3.12 -m venv .venv-eero          # macOS: /opt/homebrew/bin/python3.13 -m venv .venv-eero
source .venv-eero/bin/activate         # Windows cmd: .\.venv-eero\Scripts\activate.bat
pip install -r requirements-eero.txt
```

## Credentials

One-time login:

```bash
python -m eero_monitor login --user you@example.com
```

Copy the printed values into `.env` (from `.env.example`). On macOS/Linux:

```bash
set -a && source .env && set +a
```

On Windows, put `EERO_SESSION=...` and `EERO_NETWORK_ID=...` in `.env` directly
(cmd does not support bash `export` — see [Troubleshooting](#troubleshooting)).

Amazon-only accounts often need a secondary email/password admin in the Eero app.
Login issues (especially session errors on Windows): [Troubleshooting](#troubleshooting).
Upstream SDK notes: [eero-api wiki](https://github.com/fulviofreitas/eero-api/wiki/Troubleshooting).

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

**Setup** (Command Prompt — also works from PowerShell)

```cmd
winget install Python.Python.3.12
git clone https://github.com/andywongcheeming/py-bandwith-monitor.git
cd py-bandwith-monitor
py -3.12 -m venv .venv-eero
.\.venv-eero\Scripts\activate.bat
pip install -r requirements-eero.txt
REM copy .env from Mac or run login (see Troubleshooting)
powershell -ExecutionPolicy Bypass -File deploy\windows\serve.ps1
```

`serve.ps1` loads `.env` and starts `serve`; you can invoke it from cmd as shown
above. To run `serve` directly in cmd, activate the venv and ensure `EERO_*` are
set (e.g. via `.env` loaded manually or copied from Mac).

**Move tunnel from Mac**

1. Stop `serve` + `cloudflared` on Mac
2. Copy `~/.cloudflared/` → `C:\Users\<you>\.cloudflared\`
3. Fix `credentials-file` path in `config.yml`
4. `cloudflared tunnel run eero-monitor` on Windows

**Auto-start:** Task Scheduler runs `deploy\windows\serve.ps1`; admin PowerShell:
`cloudflared service install`. Disable Windows sleep on AC power.

## Troubleshooting

### Login session errors on Windows

`eero-api` auto-saves sessions to the OS keyring (Windows Credential Manager on
PC, macOS Keychain on Mac). On Windows, stale or partial entries are the most
common cause of session/auth errors during `python -m eero_monitor login` — often
while the same command works reliably on a Mac.

**Symptoms:** errors mentioning session tokens, `401`, or auth failures; login
may skip the `Verification code:` prompt entirely if Credential Manager holds an
old session that looks valid locally but is rejected by Eero's API.

**Fix — clear stale credentials, then retry login:**

1. **Credential Manager (GUI):** Control Panel → Credential Manager → Windows
   Credentials → remove any entry named `eero-api` (account `auth-tokens`).
2. **Or from Python** (venv activated) — save as `clear_eero_auth.py` and run
   `python clear_eero_auth.py`:

```python
import asyncio
from eero import EeroClient

async def main() -> None:
    async with EeroClient() as client:
        await client._api.auth.clear_auth_data()
    print("Cleared eero-api credentials")

asyncio.run(main())
```

Then run login again. You should see the `Verification code:` prompt.

**Easiest workaround for a Windows always-on host:** log in once on your Mac (or
where login works), copy `.env` to the Windows repo root, and use
`deploy\windows\serve.ps1` — no Windows login required. Re-copy when the session
expires (~30 days).

### Command Prompt (recommended on Windows)

Command Prompt is the supported day-to-day shell for login, troubleshooting, and
running commands. Activate the venv with **`activate.bat`**:

```cmd
cd py-bandwith-monitor
.\.venv-eero\Scripts\activate.bat
pip install -r requirements-eero.txt
pip install pywin32
python -m eero_monitor login --user you@example.com
```

`pywin32` helps Python use Windows Credential Manager reliably. Use the same venv
Python for `login` and `serve` (`where python` / `python --version` → 3.12+).

### `.env` on Windows (not `export`)

Login prints bash-style `export EERO_SESSION=...` lines. In cmd, `export` does
nothing — paste values into `.env` manually:

```env
EERO_SESSION=...
EERO_NETWORK_ID=...
```

`deploy\windows\serve.ps1` loads `.env` automatically.

### Verification and account issues

| Issue | What to try |
|-------|-------------|
| `Verification code incorrect` | Enter the code promptly; set Windows date/time to automatic |
| Amazon Sign-in account | Use a secondary email/password Eero admin — see [eero-api wiki](https://github.com/fulviofreitas/eero-api/wiki/Troubleshooting) |
| Still failing after clearing creds | Copy working `.env` from Mac; paste full stderr for diagnosis |

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
