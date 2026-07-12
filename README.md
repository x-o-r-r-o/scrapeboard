# Scrapeboard

**Scrapeboard** is a production Google Maps lead-scraping platform with:

1. **Control panel** ([`panel/`](panel/)) — FastAPI + React: users, 2FA, billing, jobs, workers, Bot Builder  
2. **Worker agents** ([`worker/`](worker/)) — scrape-only machines (Windows / macOS / Linux) that pull job chunks  
3. **Telegram bot** — optional full bot wired to the same backend (commands, payments, jobs, support)

Deploy the panel once on **HestiaCP** (OpsBoard / OmniDesk style). It runs as **systemd** until you remove it. Workers on other machines talk only to **`https://scrape.cvmso.com`**.

| Doc | Path |
|-----|------|
| Panel | [`panel/README.md`](panel/README.md) |
| Worker | [`worker/README.md`](worker/README.md) |
| HestiaCP deploy | [`deploy/hestiacp/README.md`](deploy/hestiacp/README.md) |
| Scrape engine flags | [`worker/SCRAPER.md`](worker/SCRAPER.md) |
| Short map | [`STRUCTURE.md`](STRUCTURE.md) |

---

## Table of contents

1. [Architecture](#architecture)
2. [Repository layout](#repository-layout)
3. [Features](#features)
4. [Security](#security)
5. [Quick start (local development)](#quick-start-local-development)
6. [Production deploy (HestiaCP)](#production-deploy-hestiacp)
7. [Panel setup guide](#panel-setup-guide)
8. [Worker setup](#worker-setup)
9. [Telegram Bot Builder](#telegram-bot-builder)
10. [Billing & subscriptions](#billing--subscriptions)
11. [Jobs, proxies & scrape settings](#jobs-proxies--scrape-settings)
12. [API overview](#api-overview)
13. [Operations & troubleshooting](#operations--troubleshooting)
14. [Updating](#updating)
15. [Environment reference](#environment-reference)
16. [License / notes](#license--notes)

---

## Architecture

```
Browser / Telegram
        │
        ▼
https://scrape.cvmso.com          (HestiaCP nginx + SSL)
   ├── /           → React SPA (public_html)
   └── /api/*      → 127.0.0.1:3010  Scrapeboard FastAPI (systemd)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Postgres/SQLite   Job orchestrator   Telegram runtime
         users, billing    chunk lease/ack    Bot Builder commands
                              │
                              ▼
                    Worker machines (Windows / macOS / Linux)
                    agent.py → lease → scrape → upload ZIP → ack
```

| Component | Port / URL | Role |
|-----------|------------|------|
| OpsBoard (other app) | 3000 | Do not collide |
| OmniDesk (other app) | 3001 | Do not collide |
| **Scrapeboard API** | **3010** | Panel backend |
| Scrapeboard site | `https://scrape.cvmso.com` | UI + `/api` proxy |
| Workers | outbound HTTPS only | No inbound ports required |

**Roles**

| Role | Panel | Telegram |
|------|-------|----------|
| **Admin** | Full control | Optional admin commands if enabled in Bot Builder |
| **User** | Own jobs, own stats, own subscription | Same account if Telegram ID linked |
| **Worker** | None (machine token) | None |

There is **no public registration**. Admins create users. Support is via Telegram only (if configured).

---

## Repository layout

```
scrapeboard/
├── README.md                 ← this documentation
├── STRUCTURE.md
├── deploy/                   ← HestiaCP install / update
│   ├── config.env.example
│   ├── install.sh
│   ├── update.sh
│   ├── lib/common.sh
│   └── hestiacp/
│       ├── README.md
│       ├── install.sh
│       ├── update.sh
│       └── nginx.ssl.conf_scrapeboard
├── panel/                    ← control panel (see panel/README.md)
│   ├── backend/              ← FastAPI (port 3010)
│   ├── frontend/             ← React (Vite)
│   └── data/                 ← DB, uploads, results (runtime; not in git)
└── worker/                   ← scrape agent (see worker/README.md)
    ├── agent.py              ← wizard + panel client
    ├── gmaps_scraper.py      ← browser scrape engine
    ├── setup_and_run.bat     ← Windows first-run
    ├── setup_and_run.sh      ← Linux / macOS first-run
    ├── setup_and_run.command ← macOS Finder launcher
    ├── requirements.txt
    ├── SCRAPER.md
    └── worker_config.json    ← created on first run (gitignored)
```

---

## Features

### Scraping (worker)

- Engines: Chrome (Playwright Chromium), Google Chrome, Edge, Brave, Camoufox  
- Multi-threaded browsers, proxy pools, pacing / cool-downs, stealth  
- Exhaustive Maps scroll, optional website enrich (email + socials)  
- CAPTCHA providers: none / 2captcha / CaptchaAI  
- Resumable chunked jobs, per-location CSV, ZIP delivery  
- First-run wizard + auto browser/package install (Windows / macOS / Linux)  
- Clean shutdown of browsers on stop  

### Control panel

- Invite-only users (admin create)  
- Mandatory TOTP 2FA  
- Brute-force lockout  
- reCAPTCHA **v2 or v3** (one mode at a time) on login  
- Packages, USDT TRC-20 verify, manual approve, grant/extend  
- Admin proxy pools assigned to workers  
- Worker enrollment tokens, online CPU/RAM, drain/disable  
- Jobs: upload keywords/locations, queue, progress, stop, download ZIP  
- Users only see **their own** jobs and stats  
- Scrape defaults + captcha keys (admin)  
- Telegram Bot Builder + demo workflows  

### Telegram

- Connect BotFather token from the panel  
- Toggle commands, audiences, welcome text, support chat  
- `/packages` `/buy` `/paid` `/subscription` `/run` `/status` `/stop` `/support`  
- Upload keyword/location files with captions  
- Optional admin commands: `/servers` `/pending` `/approve` `/users`  
- Results optionally delivered as Telegram documents  

---

## Security

| Control | Behavior |
|---------|----------|
| Registration | **Disabled** — admin creates accounts only |
| 2FA | **Mandatory** TOTP for admin and users |
| reCAPTCHA | Configure **either** v2 **or** v3 (not both) |
| Brute force | Lockout after N failures for M minutes (**per username+IP**) |
| Workers | Per-worker bearer token; SHA-256 indexed lookup; lease ownership enforced |
| Worker jobs | Only panel users / linked Telegram accounts can enqueue work |
| Worker uploads | Size-capped; CSV-only safe zip extract (no Zip Slip) |
| Billing | Public wallet only; TxID replay protection; rate-limited `/paid` |
| Jobs/files | Ownership checks; users cannot see others’ data |
| Production | Refuses default `SECRET_KEY` / weak bootstrap password when `ENVIRONMENT=production` or HTTPS `PUBLIC_URL` |
| Secrets | `.env` / `config.env` / `worker_config.json` never committed |

---

## Quick start (local development)

Use this only for development. Production uses HestiaCP + systemd (below).

### Prerequisites

- Python 3.10+  
- Node.js 18+ or Bun (frontend)  
- For workers: first run auto-installs Playwright / browsers as needed  

### 1. Panel API

```bash
cd panel/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit SECRET_KEY and bootstrap password
uvicorn app.main:app --reload --host 127.0.0.1 --port 3010
```

```bash
curl -s http://127.0.0.1:3010/api/health
# {"ok":true}
```

| Field | Default (see `.env`) |
|-------|----------------------|
| Username | `admin` |
| Password | from `BOOTSTRAP_ADMIN_PASSWORD` |
| After login | Must change password + enable 2FA |

### 2. Panel UI

```bash
cd panel/frontend
npm install          # or: bun install
npm run dev          # http://127.0.0.1:5173  (proxies /api → :3010)
```

### 3. Local worker (optional)

In the panel: **Admin → Workers → Create** → copy token.

```bash
cd worker
# easiest: setup_and_run.sh / .bat / .command
# or:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py --panel-url http://127.0.0.1:3010 --token YOUR_TOKEN
# first run without flags opens the wizard (use panel URL http://127.0.0.1:3010)
```

More: [`worker/README.md`](worker/README.md) · [`panel/README.md`](panel/README.md)

---

## Production deploy (HestiaCP)

Same pattern as OpsBoard: **static SPA in `public_html` + systemd API + nginx `/api` proxy**.

### Defaults

| Setting | Value |
|---------|-------|
| Hestia user | `cvmso` |
| Domain | `scrape.cvmso.com` |
| App dir | `/home/cvmso/apps/scrapeboard` |
| Public HTML | `/home/cvmso/web/scrape.cvmso.com/public_html` |
| API bind | `127.0.0.1:3010` |
| systemd unit | `scrapeboard.service` |

### Part 1 — HestiaCP UI

1. Log in to HestiaCP  
2. **WEB → Add Web Domain** → `scrape.cvmso.com`  
3. Enable **SSL / Let's Encrypt**  
4. Wait until the certificate is valid  

### Part 2 — Upload code

```bash
ssh root@YOUR_SERVER_IP 'mkdir -p /home/cvmso/apps'
rsync -az --delete \
  --exclude node_modules --exclude .venv --exclude panel/data \
  --exclude '__pycache__' --exclude .git --exclude dist \
  ./ root@YOUR_SERVER_IP:/home/cvmso/apps/scrapeboard/
```

Or set `REPO_URL` in `deploy/config.env` and let `install.sh` `git clone`.

### Part 3 — Install (once, as root)

```bash
ssh root@YOUR_SERVER_IP
cd /home/cvmso/apps/scrapeboard
cp deploy/config.env.example deploy/config.env
# defaults already match cvmso / scrape.cvmso.com / 3010
# set BOOTSTRAP_ADMIN_PASSWORD to a strong value

bash deploy/hestiacp/install.sh
```

What the installer does:

1. Installs system packages (Python, git, rsync, …)  
2. Installs **Bun** (frontend build)  
3. Creates Python venv + installs API requirements  
4. Builds React → rsync into `public_html`  
5. Writes `panel/backend/.env`  
6. Enables and starts **systemd `scrapeboard`**  
7. Installs nginx snippet `nginx.ssl.conf_scrapeboard`  
8. Runs `v-rebuild-web-domain cvmso scrape.cvmso.com`  
9. Health-checks `http://127.0.0.1:3010/api/health`  

Open **https://scrape.cvmso.com** → sign in → change password → enable 2FA.

Deep dive: [`deploy/hestiacp/README.md`](deploy/hestiacp/README.md).

### Service commands

```bash
systemctl status scrapeboard
journalctl -u scrapeboard -f
systemctl restart scrapeboard
systemctl stop scrapeboard
systemctl disable --now scrapeboard    # stop autostart
```

### Nginx note

The custom snippet only adds `location /api/` and SPA `error_page 404`.  
**Do not** add a second `location /` — Hestia already owns `/` and duplicates break nginx.

---

## Panel setup guide

After first login (password + 2FA done):

### 1. Security (Admin → Security)

- Choose reCAPTCHA mode: `none` | `v2` | `v3` (only one)  
- Paste site key + secret  
- For v3, set minimum score (e.g. `0.5`)  
- Tune lockout: max failures + minutes  

### 2. Users (Admin → Users)

```
Create user → username, email, temp password, role (user|admin), optional Telegram ID
```

User must change password and enable 2FA on first login.  
Link Telegram ID so the bot can authorize them.

### 3. Packages & billing (Admin → Packages / Billing)

1. Create packages (slug, price USDT, days, threads, upload MB, tier)  
2. **Billing**:
   - Enable billing  
   - USDT TRC-20: receiving wallet (+ optional TronScan API key)  
   - Manual methods JSON, e.g.:

```json
[
  {"name": "Bank Transfer", "details": "Bank X, IBAN YY, Ref: your user id"}
]
```

3. **Grant** a package to a user, or let them **Buy** + `/paid` / panel TxID verify  
4. **Pending orders** → Approve (manual payments)  

### 4. Proxy pools (Admin → Proxy pools)

Paste proxies (one per line), same formats as the scraper:

```
host:port
host:port:user:pass
user:pass@host:port
socks5://user:pass@host:port
```

Assign a pool to each worker.

### 5. Workers (Admin → Workers)

1. Create worker → set max browsers + optional proxy pool → **copy token once**  
2. Click **Settings** to edit per-worker scrape flags (engine, threads, delays, headless, captcha, …)  
3. New workers copy **global Scrape settings** into their `worker_config`; you can override per machine  
4. Flags are sent in every lease and synced into the agent’s local `worker_config.json` (`scrape` key) on heartbeat  
5. Threads are capped by **max browsers**  
6. Optional: drain / disable / rotate token  

Install hint:

```bash
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token TOKEN
```

### 6. Scrape settings (Admin → Scrape settings)

Global defaults sent inside job leases: engine, threads, delays, captcha provider/key, chunk size, etc.

### 7. Bot Builder (Admin → Bot builder)

See [Telegram Bot Builder](#telegram-bot-builder).

### 8. User flow (Jobs)

1. Active subscription (or admin)  
2. **Jobs → New job** → upload `keywords.txt` + `locations.txt`  
3. Optional engine/threads overrides (capped by plan)  
4. Watch progress; **Stop** or wait for complete → **Download** ZIP  

UI route map and API notes: [`panel/README.md`](panel/README.md).

---

## Worker setup

Workers are **scrape-only** and run on **Windows, macOS, or Linux**.

### First run (interactive setup + auto browser install)

| OS | Command |
|----|---------|
| **Windows** | Double-click `worker/setup_and_run.bat` |
| **macOS** | Double-click `worker/setup_and_run.command` (or `bash setup_and_run.sh`) |
| **Linux** | `bash worker/setup_and_run.sh` |

Or manually:

```bash
cd worker
python3 -m venv .venv && source .venv/bin/activate   # Win: py -3 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python agent.py                # wizard → saves worker_config.json
python agent.py --selftest     # optional: verify browser stack
```

Wizard asks for panel URL (`https://scrape.cvmso.com`), worker token, name, default engine.  
Browsers/packages auto-install per engine on first use (same as the original scraper).

```bash
python agent.py --setup          # re-run wizard
python agent.py --force-setup    # re-install browsers
python agent.py --skip-setup     # never auto-install
```

Keep it running under `tmux`, `screen`, systemd, LaunchAgent, or a Windows service.

### What the agent does

1. `POST /api/worker-api/heartbeat` (CPU/RAM)  
2. `POST /api/worker-api/lease` → chunk + keywords/locations + settings + proxies  
3. Runs `gmaps_scraper` for that chunk  
4. Zips CSV parts → `POST /api/worker-api/upload`  
5. `POST /api/worker-api/ack` → panel merges when all chunks done → user ZIP + optional Telegram  

Full worker guide: [`worker/README.md`](worker/README.md).  
Engine flags: [`worker/SCRAPER.md`](worker/SCRAPER.md).

### Standalone engine diagnostics

```bash
cd worker
python gmaps_scraper.py --selftest --engine chrome
python gmaps_scraper.py --check-proxies --proxies proxies.txt
python gmaps_scraper.py --diagnose --proxy-index 0 --engine chrome
```

In production, prefer **`agent.py`** against the panel.

---

## Telegram Bot Builder

### Connect the bot

1. Message **@BotFather** → `/newbot` → copy token  
2. Panel → **Bot Builder**:
   - Paste token  
   - Enable bot  
   - Set welcome text, notify interval  
   - Optional: support enabled + support chat ID  
   - Optional: public `/packages`, deliver results via Telegram  
   - Optional: admin Telegram commands  
3. Save → runtime restarts  

### Install demo workflows

Click **Install / refresh demos** to load onboarding, USDT buy, manual buy, job run/stop, expiry reminder, support, worker-down, payment-failed templates.

### Commands (toggle per command in Bot Builder)

| Command | Who | Purpose |
|---------|-----|---------|
| `/start` `/help` | everyone / gated | Welcome & help |
| `/whoami` | everyone | Telegram id + link status |
| `/packages` | everyone* | List plans |
| `/buy <slug>` | linked users | Create order + payment instructions |
| `/paid <txid>` | linked users | On-chain USDT verify |
| `/subscription` | users | Own plan |
| `/run [k=v…]` | subscribers | Queue job from uploaded inputs |
| `/status` | users | **Own jobs only** |
| `/stop` | subscribers | Stop own job + partial ZIP |
| `/support …` | users | Ticket → support chat |
| `/servers` | admins** | Workers |
| `/pending` | admins** | Pending orders |
| `/approve <order_id>` | admins** | Approve manual order |
| `/users` | admins** | List users |

\* if “public packages” enabled  
\*\* only if “admin Telegram commands” enabled  

### Upload inputs in Telegram

Send a `.txt` / `.csv` document with caption:

- `keywords`  
- `locations`  

Then `/run engine=chrome threads=2`.

### Link a Telegram user

Admin → Users → set **Telegram ID** (user can get it via `/whoami` on the bot).

---

## Billing & subscriptions

### Package fields

- `slug`, `name`, `tier`, `price_usdt`, `duration_days`  
- `threads`, `max_upload_mb`, `allowed_engines`, `is_active`  

### Rules

- Active subscription required to run jobs (admins bypass)  
- **Upgrade-only** while subscribed (same or higher tier)  
- Thread and upload caps enforced from package + user perms  
- USDT: verify recipient, contract, amount, confirmation; TxID single-use  
- Manual: user pays → admin Approves in panel or `/approve`  

### Panel endpoints (authenticated)

```text
GET  /api/packages
POST /api/orders/buy          {"package_slug":"pro"}
POST /api/orders/paid         {"txid":"..."}
GET  /api/subscriptions/me
POST /api/subscriptions/grant {"user_id":2,"package_id":1}   # admin
GET  /api/orders/pending                                    # admin
POST /api/orders/approve      {"order_id":1}                # admin
GET  /api/billing/settings                                  # admin
PUT  /api/billing/settings
GET  /api/billing/public
```

---

## Jobs, proxies & scrape settings

### Create a job (panel)

`POST /api/jobs` multipart:

- `keywords` file  
- `locations` file  
- optional `engine`, `threads`, `scrape_websites`, `max_results`  

### Job statuses

`queued` → `running` → `completed` | `stopped` | `failed`

### Download

`GET /api/jobs/{id}/download` → ZIP of merged per-location CSVs  

### Worker protocol

```text
POST /api/worker-api/heartbeat   Authorization: Bearer <worker-token>
POST /api/worker-api/lease
POST /api/worker-api/upload?job_id=&chunk_id=   (multipart file)
POST /api/worker-api/ack         {"job_id","chunk_id","rows"}
```

---

## API overview

Base URL (production): `https://scrape.cvmso.com/api`  
Auth: `Authorization: Bearer <access_token>` after login.

### Auth

```text
GET  /api/auth/public-config     # recaptcha mode/site key; registration_enabled=false
POST /api/auth/login             # username, password, totp_code?, recaptcha_token?
GET  /api/auth/me
POST /api/auth/change-password
POST /api/auth/2fa/setup
POST /api/auth/2fa/enable
GET  /api/auth/ready
GET  /api/health
```

### Admin modules

```text
/api/users
/api/packages
/api/billing/*
/api/proxy-pools
/api/workers
/api/settings/scrape
/api/settings/security
/api/bot/settings
/api/bot/commands
/api/bot/workflows
/api/bot/install-demos
/api/bot/restart
```

OpenAPI docs when API is running: `http://127.0.0.1:3010/docs` (localhost only in production bind).

---

## Operations & troubleshooting

| Problem | Fix |
|---------|-----|
| 502 on `/api` | `systemctl status scrapeboard`; `journalctl -u scrapeboard -n 100` |
| Blank page | Rebuild frontend + rsync (`update.sh`) |
| Login lockout | Wait lockout window or clear `login_attempts` / adjust Security settings |
| 2FA fails | Server time NTP (`timedatectl`); re-setup 2FA if secret changed |
| Worker offline | Check token, HTTPS URL, firewall outbound; re-run `agent.py --setup` |
| Worker browser missing | `python agent.py --force-setup` or `--selftest --engine chrome` |
| nginx won’t start | Remove duplicate `location /`; `nginx -t` |
| Port in use | Change `API_PORT` in `deploy/config.env` (avoid 3000/3001), re-run update |
| Permission denied | `chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard` |

### Important paths on VPS

```text
/home/cvmso/apps/scrapeboard/
/home/cvmso/apps/scrapeboard/panel/backend/.env
/home/cvmso/apps/scrapeboard/panel/data/panel.db
/home/cvmso/web/scrape.cvmso.com/public_html/
/home/cvmso/conf/web/scrape.cvmso.com/nginx.ssl.conf_scrapeboard
/etc/systemd/system/scrapeboard.service
```

---

## Updating

```bash
# sync new code into APP_DIR, then as root:
bash /home/cvmso/apps/scrapeboard/deploy/hestiacp/update.sh
```

Update **keeps** existing `panel/backend/.env` (secrets preserved), rebuilds frontend, restarts systemd, refreshes nginx snippet.

Workers: pull/sync the `worker/` folder on each machine, then restart `agent.py` (config file stays).

---

## Environment reference

### `panel/backend/.env`

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | JWT signing |
| `DATABASE_URL` | SQLite/async URL |
| `CORS_ORIGINS` | Comma-separated origins |
| `BOOTSTRAP_ADMIN_*` | First admin seed |
| `PUBLIC_URL` | Public site URL |
| `API_PORT` | Listen port (3010) |

### `deploy/config.env`

See `deploy/config.env.example` — Hestia user, domain, port, bootstrap password, optional `REPO_URL`.

### `worker/worker_config.json`

Created by the wizard (`panel_url`, `token`, `name`, `engine`). Gitignored.

---

## License / notes

- Scrapeboard panel + worker packaging for private deployment.  
- Respect Google Maps / website Terms of Service and local law when scraping.  
- Never commit `.env`, `config.env`, database files, or worker tokens.  
- Engine deep-dive: [`worker/SCRAPER.md`](worker/SCRAPER.md)  
- Deploy deep-dive: [`deploy/hestiacp/README.md`](deploy/hestiacp/README.md)  
