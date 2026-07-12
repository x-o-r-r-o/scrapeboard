# Scrapeboard

**Scrapeboard** is a production Google Maps lead-scraping platform with:

1. **Control panel** ([`panel/`](panel/)) — FastAPI + React: users, 2FA, billing, jobs, workers, Bot Builder  
2. **Worker agents** ([`worker/`](worker/)) — scrape-only machines (Windows / macOS / Linux) that pull job chunks  
3. **Telegram bot** — optional full bot wired to the same backend (commands, payments, jobs, support)

Deploy the panel once on **HestiaCP** (OpsBoard / OmniDesk style). It runs as **systemd** until you remove it. Workers on other machines talk only to **`https://scrape.cvmso.com`**.

| Doc | Path |
|-----|------|
| **Run by default** | [below](#run-by-default) |
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
5. [Run by default](#run-by-default)
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
├── install.py / install.sh / install.bat / install.command
│                             ← **start here** — panel vs worker by OS
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
│   ├── run.sh                ← local API start helper
│   ├── backend/              ← FastAPI (port 3010)
│   ├── frontend/             ← React (Vite)
│   └── data/                 ← DB, uploads, results (runtime; not in git)
└── worker/                   ← scrape agent (see worker/README.md)
    ├── agent.py              ← wizard + panel client
    ├── gmaps_scraper.py      ← browser scrape engine
    ├── setup_and_run.*       ← first-run wizard (+ optional service install)
    ├── install_service.*     ← **default** background service (login/boot)
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
- Scrape defaults (admin)  
- Global captcha solvers primary + backup (admin)  
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

## Run by default

**Start here on a fresh machine:**

| OS | Command |
|----|---------|
| **macOS / Linux** | `./install.sh` (or `bash install.sh`) |
| **macOS (Finder)** | Double-click `install.command` |
| **Windows** | Double-click `install.bat` (or run it from cmd) |
| **Any (Python)** | `python3 install.py` |

The installer detects the OS, asks **control panel** or **worker**, then launches the right path:

| Choice | Linux | macOS | Windows |
|--------|-------|-------|---------|
| **Control panel → production** | `deploy/hestiacp/install.sh` (HestiaCP; root) | Not available — use Linux/Hestia | Not available — use Linux/Hestia |
| **Control panel → local** | `panel/run.sh --reload` + frontend steps | Same | Backend venv + uvicorn + frontend steps |
| **Worker** | `worker/setup_and_run.sh` → optional `install_service.sh` | Same (or `.command`) | `worker/setup_and_run.bat` → optional `install_service.bat` |

Production panel = HestiaCP + systemd (Linux only). Workers = **background service** (`install_service.*`) after the first-run wizard.

### Prerequisites

| Need | Notes |
|------|--------|
| Python **3.10+** | Panel API + worker (`python3` / Windows `py -3`) |
| Node.js **18+** or Bun | Panel frontend (local) or Bun on VPS via installer |
| Outbound HTTPS | Workers → panel; no inbound ports on workers |
| Browsers | Auto-installed on first worker engine use (Playwright / Brave / …) |

### Default operational workflow

1. **`./install.sh` / `install.bat`** → control panel (Hestia prod, or local API + UI) **or** worker.  
2. Admin: password + 2FA → packages / billing → proxy pools → **Workers → Create** → **copy token once**.  
3. Optional: **Bot Builder** → BotFather token → **Install / refresh demos**.  
4. On each scrape machine: installer → worker (or `setup_and_run.*`) → wizard → **`install_service.*`** (recommended).  
5. Admin creates users (optional Telegram ID); grant package / user buys via Telegram.  
6. User runs jobs in **Telegram** (`/run`) or **panel Jobs**; admin monitors workers/jobs in the panel.  
7. User **`/stop`** (or panel Stop) to cancel; results ZIP via panel / optional Telegram delivery.

### A. Production panel (HestiaCP) — default for scrape.cvmso.com

On a Hestia Linux host you can also run `./install.sh` → **Control panel** → **Production**.

```bash
# as root on the VPS
mkdir -p /home/cvmso/apps && cd /home/cvmso/apps
git clone https://github.com/x-o-r-r-o/scrapeboard.git scrapeboard
chown -R cvmso:cvmso scrapeboard
git config --global --add safe.directory /home/cvmso/apps/scrapeboard

cd scrapeboard
cp deploy/config.env.example deploy/config.env
nano deploy/config.env   # BOOTSTRAP_ADMIN_PASSWORD='…' (single-quoted)

bash deploy/hestiacp/install.sh
# or: ./install.sh  → Control panel → Production
```

Then open **https://scrape.cvmso.com** → sign in → change password → enable 2FA.

| Day-to-day | Command |
|------------|---------|
| Status | `systemctl status scrapeboard` |
| Logs | `journalctl -u scrapeboard -f` |
| Restart | `systemctl restart scrapeboard` |
| Stop | `systemctl stop scrapeboard` |
| Update | `bash deploy/hestiacp/update.sh` (pulls with sparse-checkout; excludes `worker/`) |

Telegram bot runs **inside the panel API process** (no separate bot service). Full steps: [`deploy/hestiacp/README.md`](deploy/hestiacp/README.md).

### B. Local panel (development)

DB tables + bootstrap admin are created automatically on API start (no separate migrate step).

**1. Backend** (port **3010**):

```bash
# helper (creates venv, installs deps, starts uvicorn):
bash panel/run.sh --reload

# or manually:
cd panel/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # edit SECRET_KEY + BOOTSTRAP_ADMIN_PASSWORD
uvicorn app.main:app --reload --host 127.0.0.1 --port 3010
```

```bash
curl -s http://127.0.0.1:3010/api/health
# {"ok":true}
```

| Field | Default |
|-------|---------|
| Username | `admin` |
| Password | `BOOTSTRAP_ADMIN_PASSWORD` in `.env` |
| After login | Change password + enable 2FA |

**2. Frontend:**

```bash
cd panel/frontend
npm install          # or: bun install
npm run dev          # http://127.0.0.1:5173  (proxies /api → :3010)
```

Production UI is a static build into Hestia `public_html` (not `npm run dev`).

### C. Worker (default: background service)

Preferred: from repo root run `./install.sh` / `install.bat` → **Worker**.

1. Panel → **Admin → Workers → Create** → set max browsers / proxy pool → **copy token once**.  
2. First run on the scrape machine:

| OS | First run |
|----|-----------|
| **Windows** | Double-click `worker/setup_and_run.bat` |
| **macOS** | Double-click `worker/setup_and_run.command` or `bash worker/setup_and_run.sh` |
| **Linux** | `bash worker/setup_and_run.sh` |

Wizard: panel URL (`https://scrape.cvmso.com` or `http://127.0.0.1:3010`), token, name, engine. Saves `worker_config.json`.

3. **Install background service** (recommended default — scripts offer this after setup):

```bash
# macOS / Linux
cd worker && bash install_service.sh

# Windows
cd worker && install_service.bat
```

| OS | Status | Logs | Stop / uninstall |
|----|--------|------|------------------|
| **macOS** | `launchctl print gui/$(id -u)/com.scrapeboard.worker \| head` | `worker/logs/worker.log` | `bash install_service.sh --uninstall` |
| **Linux** | `systemctl --user status scrapeboard-worker` | `logs/worker.log` or `journalctl --user -u scrapeboard-worker -f` | `bash install_service.sh --uninstall` |
| **Windows** | `schtasks /Query /TN ScrapeboardWorker` | `logs\worker.log` | `install_service.bat --uninstall` |

Linux after logout / at boot without a session: `sudo loginctl enable-linger "$USER"` (installer tries once).

Foreground only (dev): `python agent.py` after config exists.

### D. User & admin day-to-day

| Who | Where | Actions |
|-----|-------|---------|
| **Admin** | Panel | Users, packages, billing, proxies, workers, scrape defaults, Bot Builder, monitor jobs |
| **User** | Telegram | `/packages` `/buy` `/paid` `/subscription` `/run` `/status` `/stop` `/support` |
| **User** | Panel | Jobs → New / Stop / Download; Subscription |
| **Worker** | Machine | Service waits for leases; no UI |

Link Telegram: user `/whoami` → admin sets **Telegram ID** on the user.

More: [`panel/README.md`](panel/README.md) · [`worker/README.md`](worker/README.md)

---

## Production deploy (HestiaCP)

Same pattern as OpsBoard: **static SPA in `public_html` + systemd API + nginx `/api` proxy**.

**Full step-by-step with every command:** [`deploy/hestiacp/README.md`](deploy/hestiacp/README.md)

### Defaults

| Setting | Value |
|---------|-------|
| Hestia user | `cvmso` |
| Domain | `scrape.cvmso.com` |
| App dir | `/home/cvmso/apps/scrapeboard` |
| Public HTML | `/home/cvmso/web/scrape.cvmso.com/public_html` |
| API bind | `127.0.0.1:3010` |
| systemd unit | `scrapeboard.service` |

### Quick install (as root on the VPS)

```bash
mkdir -p /home/cvmso/apps
cd /home/cvmso/apps
git clone https://github.com/x-o-r-r-o/scrapeboard.git scrapeboard
chown -R cvmso:cvmso scrapeboard
git config --global --add safe.directory /home/cvmso/apps/scrapeboard

cd scrapeboard
cp deploy/config.env.example deploy/config.env
nano deploy/config.env   # set BOOTSTRAP_ADMIN_PASSWORD (single-quoted)

bash deploy/hestiacp/install.sh
```

Then open **https://scrape.cvmso.com** → sign in → change password → enable 2FA.

If login fails after an older install (password with `#` truncated):

```bash
bash deploy/hestiacp/reset_admin_password.sh 'YourNewPassword'
systemctl restart scrapeboard
```

Deep dive (DNS, Hestia UI, rsync, update, nginx, workers, troubleshooting):  
[`deploy/hestiacp/README.md`](deploy/hestiacp/README.md)

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
2. Click **Settings** to edit per-worker scrape flags (engine, threads, delays, headless, …)  
3. New workers copy **scrape profile** flags into their `worker_config`; you can override per machine  
4. Flags are sent in every lease and synced into the agent’s local `worker_config.json` (`scrape` key) on heartbeat  
5. Captcha solvers come from **Admin → Captcha** (global), not per worker/profile  
6. Optional: drain / disable / rotate token  

Install hint (panel shows this when creating a worker):

```bash
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token TOKEN
# then (default):
#   macOS/Linux: bash install_service.sh
#   Windows:     install_service.bat
```

### 6. Scrape profiles & 2captcha / CaptchaAI (Admin)

- **Scrape profiles** — engine, threads, delays, chunk size, headless, etc. Assign to workers and packages.  
- **2captcha / CaptchaAI** — global primary + backup solvers. Applied to all job leases.  

### 7. Bot Builder (Admin → Bot builder)

See [Telegram Bot Builder](#telegram-bot-builder).

### 8. User flow (Jobs)

1. Active subscription (or admin)  
2. **Jobs → New job** → upload `keywords.txt` + `locations.txt`  
3. Optional engine/threads (each job’s threads must be ≤ plan allowance)  
4. **Shared thread pool:** concurrent jobs share your allowance. If you request more threads than are free, the job stays **queued** until capacity frees (or edit the queued job’s threads to fit).  
5. Watch progress; **Stop** or wait for complete → **Download** ZIP  

UI route map and API notes: [`panel/README.md`](panel/README.md).

---

## Worker setup

Workers are **scrape-only** on **Windows, macOS, or Linux**. **Default run mode:** background service after the wizard (see [Run by default → C](#c-worker-default-background-service)).

### First run → service

| OS | First run | Then (default) |
|----|-----------|----------------|
| **Windows** | `worker/setup_and_run.bat` | `install_service.bat` |
| **macOS** | `setup_and_run.command` / `bash setup_and_run.sh` | `bash install_service.sh` |
| **Linux** | `bash setup_and_run.sh` | `bash install_service.sh` (+ linger if needed) |

Or manually:

```bash
cd worker
python3 -m venv .venv && source .venv/bin/activate   # Win: py -3 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python agent.py                # wizard → worker_config.json
python agent.py --selftest     # optional
bash install_service.sh        # Windows: install_service.bat
```

Wizard: panel URL (`https://scrape.cvmso.com`), worker token (Admin → Workers), name, engine.  
Browsers/packages auto-install per engine on first use.

```bash
python agent.py --setup          # re-run wizard
python agent.py --force-setup    # re-install browsers
python agent.py --skip-setup     # never auto-install
```

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

In production, prefer **`agent.py`** against the panel (as a service).

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

`threads` for a single job cannot exceed the user’s allowance (`min(perms.max_threads, subscription.threads)`).  
Across **all running jobs**, the sum of threads must stay ≤ that allowance. Extra jobs remain `queued` until free threads cover them.

`GET /api/jobs/quota` → `{ thread_allowance, threads_in_use, threads_free }`  
`PATCH /api/jobs/{id}` (queued only) → `{ threads?, engine? }` to lower threads so a waiting job can start.

### Job statuses

`queued` → `running` → `completed` | `stopped` | `failed`

Queued jobs may show **waiting for free threads** when the owner’s pool is full.

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
/api/scrape-profiles
/api/settings/scrape
/api/settings/captcha
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
# Panel VPS — sync code, then as root:
bash /home/cvmso/apps/scrapeboard/deploy/hestiacp/update.sh
```

Update **keeps** existing `panel/backend/.env` (secrets preserved), rebuilds frontend, restarts systemd, refreshes nginx snippet.

**Workers** (each scrape machine):

```bash
cd worker
git pull --ff-only                 # or sync the worker/ folder
source .venv/bin/activate          # Win: .venv\Scripts\activate
pip install -r requirements.txt
# restart service (config + token stay in worker_config.json):
bash install_service.sh            # re-install/restart LaunchAgent / systemd user unit
# Windows: install_service.bat
```

Or uninstall → pull → reinstall: `bash install_service.sh --uninstall` then `bash install_service.sh`.

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
