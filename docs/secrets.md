# Secrets and local config (public repo)

This repository is intended to be **public**. Nothing in git should contain real
credentials, tokens, or session cookies.

## Safe to commit

| File | Purpose |
|------|---------|
| `config.example.yaml` | Template with `null` placeholders |
| `.env.example` | Env var names only — copy to `.env` locally |
| Docs | Placeholders like `example.com` only |

## Never commit (gitignored)

| Path / variable | What it is |
|-----------------|------------|
| `.env`, `.env.local`, … | Local env files with real values |
| `config.yaml` | Agent token, webhook URL, etc. |
| `monitor.db`, `eero_monitor.db` | Runtime SQLite |
| `~/.cloudflared/*.json` | Cloudflare tunnel credentials |
| `EERO_SESSION`, `EERO_NETWORK_ID` | Eero API session |
| `MONITOR_AGENT_TOKEN` | Agent ingest bearer token |

## Workflow

```bash
cp config.example.yaml config.yaml    # main monitor (optional)
cp .env.example .env                  # Eero / env secrets (optional)
# edit locally; never git add them
```

Load Eero env vars:

```bash
set -a && source .env && set +a
python -m eero_monitor serve
```

For systemd or `launchd`, use `EnvironmentFile=` pointing **outside** the repo
(e.g. `/etc/bandwidth-monitor/env`).

## Before pushing

Run `git status` and confirm no `.env`, `config.yaml`, or `*.db` is staged. If a
secret was ever committed, **rotate it** (new Eero login, new agent token) —
history rewrites do not revoke leaked tokens.
