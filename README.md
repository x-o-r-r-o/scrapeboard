# Scrapeboard

**Scrapeboard** is a production multi-source lead-scraping platform with:

1. **Control panel** ([`panel/`](panel/)) — FastAPI + React: users, 2FA, billing, jobs, scrapers, workers, Bot Builder  
2. **Worker agents** ([`worker/`](worker/)) — scrape-only machines (Windows / macOS / Linux) that pull job chunks for Maps, Search, email, TikTok Shop, Facebook, and social sources  
3. **Telegram bot** — optional full bot wired to the same backend (commands, payments, jobs, support)

**Telegram end users:** send **`/help`** on the bot (attaches [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md)). Also `/scrapers` and `/support`.

Deploy the panel once on **HestiaCP** (OpsBoard / OmniDesk style). It runs as **systemd** until you remove it. Workers on other machines talk only to **`https://scrape.cvmso.com`**.

| Doc | Path |
|-----|------|
| **Run by default** | [below](#run-by-default) |
| **Telegram users** | [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md) |
| Panel | [`panel/README.md`](panel/README.md) |
| Worker | [`worker/README.md`](worker/README.md) |
| HestiaCP deploy | [`deploy/hestiacp/README.md`](deploy/hestiacp/README.md) |
| Maps engine flags | [`worker/SCRAPER.md`](worker/SCRAPER.md) |
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
10. [Scraper modules](#scraper-modules)
11. [Billing & subscriptions](#billing--subscriptions)
12. [Jobs, proxies & scrape settings](#jobs-proxies--scrape-settings)
13. [API overview](#api-overview)
14. [Operations & troubleshooting](#operations--troubleshooting)
15. [Updating](#updating)
16. [Environment reference](#environment-reference)
17. [License / notes](#license--notes)

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

There is **no public registration**. Admins create users. Support tickets open via Telegram (`/support`) and can be managed from Telegram admin commands or **Admin → Support** in the panel; users get replies instantly on Telegram.

---

## Repository layout

```
scrapeboard/
├── README.md                 ← this documentation
├── TELEGRAM_USERS.md         ← Telegram end-user guide (all scrapers)
├── STRUCTURE.md
├── .scrapeboard-role         ← local machine role panel|worker (gitignored)
├── install.py / install.sh / install.bat / install.command
│                             ← **start here** — panel vs worker by OS; --update honors role
├── deploy/                   ← HestiaCP install / update
│   ├── config.env.example
│   ├── install.sh
│   ├── update.sh
│   ├── lib/common.sh
│   ├── lib/role.sh           ← role file + sparse-checkout helpers
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
    ├── agent.py              ← wizard + panel client + multi-source dispatch
    ├── gmaps_scraper.py      ← Maps engine (+ shared browser bootstrap)
    ├── google_search_scraper.py / email_* / tiktok_shop_* / social_* / meta_*
    ├── setup_and_run.*       ← first-run wizard (+ optional service install)
    ├── install_service.*     ← **default** background service (login/boot)
    ├── update.sh / update.bat← worker-role git sync (no panel/)
    ├── requirements.txt
    ├── SCRAPER.md            ← Maps engine flags
    └── worker_config.json    ← created on first run (gitignored)
```

End-user Telegram guide: [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md).

---

## Features

### Scraping (worker)

- **Multi-source:** Google Maps (`gmaps`), Google Search (+ dorks), email harvest/validate, TikTok Shop, YouTube, Reddit, Pinterest, TikTok, Facebook (pages/groups/posts/comments), Instagram, LinkedIn, X/Twitter — see [Scraper modules](#scraper-modules)  
- Engines: Chrome (Playwright Chromium), Google Chrome, Edge, Brave, Camoufox  
- Multi-threaded browsers, proxy pools, pacing / cool-downs, stealth  
- Maps: exhaustive scroll, optional website enrich (email + socials)  
- CAPTCHA providers: none / 2captcha / CaptchaAI  
- Resumable chunked jobs, CSV + ZIP delivery  
- First-run wizard + auto browser/package install (Windows / macOS / Linux)  
- Clean shutdown of browsers on stop  

### Control panel

- Invite-only users (admin create)  
- Mandatory TOTP 2FA  
- Brute-force lockout  
- reCAPTCHA **v2 or v3** (one mode at a time) on login  
- Packages, USDT TRC-20 verify, USDT BEP-20 QR + admin approve, grant/extend
- `/start` auto-creates a panel user linked to the Telegram id and shows packages to buy  
- Admin proxy pools assigned to workers  
- Worker enrollment tokens, online CPU/RAM, drain/disable  
- Jobs: pick scraper, upload inputs, queue, progress, stop, download ZIP  
- Package **allowed sources** + Admin **Scrapers** enable flags  
- Users only see **their own** jobs and stats  
- Scrape defaults (admin)  
- Global captcha solvers primary + backup (admin)  
- Telegram Bot Builder + demo workflows  

### Telegram

- Connect BotFather token from the panel  
- Toggle commands, audiences, welcome text, support chat  
- `/packages` `/buy` `/paid` `/subscription` `/run` `/status` `/stop` `/support`  
- `/help` — commands + support tickets + attaches Telegram user guide  
- `/scrapers` — allowed scraper modules  
- Upload keyword / location / email files with captions  
- `/run source=…` for any enabled module (Maps default)  
- **User guide:** [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md)  
- **Telegram admin** (`role=admin` + linked `telegram_id` + Bot Builder admin commands): `/admin` menu — see [`panel/README.md`](panel/README.md#telegram-admin)  
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

The installer detects the OS, asks **control panel** or **worker**, then launches the right path. With **`--yes` / `-y`** (or `SCRAPEBOARD_ASSUME_YES=1`) it uses noninteractive defaults after the role is known.

| Choice | Linux | macOS | Windows |
|--------|-------|-------|---------|
| **Control panel → production** | `deploy/hestiacp/install.sh` (HestiaCP; root) | Not available — use Linux/Hestia | Not available — use Linux/Hestia |
| **Control panel → local** | venv + deps + frontend npm/bun; `panel/run.sh` | Same | Backend venv + uvicorn + frontend npm |
| **Worker** | auto python/venv/deps → wizard → optional service | brew python if needed → same | winget Python if needed → same |

Production panel = HestiaCP + systemd (Linux only). Workers = **background service** (`install_service.*`) after the first-run wizard (auto-installed when `--yes` and config exists).

### Fully automatic (noninteractive)

Role must be set via `--role` or an existing `.scrapeboard-role`. Worker credentials still need env vars (or an existing `worker_config.json`):

```bash
# Worker (Linux / macOS)
export SCRAPEBOARD_PANEL_URL=https://scrape.example
export SCRAPEBOARD_TOKEN='…'   # Admin → Workers → Create (shown once)
./install.sh --role worker --yes
# Optional mesh VPN package only (login still manual):  --tailscale

# Worker (Windows)
set SCRAPEBOARD_PANEL_URL=https://scrape.example
set SCRAPEBOARD_TOKEN=...
install.bat --role worker --yes

# Local panel (any OS) — creates venv, deps, .env secrets; does not start API under --yes
python3 install.py --role panel --yes

# Hestia production (Linux root, after deploy/config.env exists)
python3 install.py --role panel --yes
```

| Still manual | Why |
|--------------|-----|
| Worker token / panel URL | Secrets from the panel UI (or set `SCRAPEBOARD_PANEL_URL` + `SCRAPEBOARD_TOKEN`) |
| `tailscale up` | Browser/admin login — never forced |
| Hestia domain + DNS | Domain must exist in Hestia; DNS must point at the VPS |
| Homebrew on macOS | Printed install URL — not silently curled unless you install brew yourself |
| Admin password / 2FA | Change after first panel login |

### Prerequisites

| Need | Notes |
|------|--------|
| Python **3.10+** | Auto-installed when possible (apt / brew `python@3.12` / winget `Python.Python.3.12`) |
| Node.js **18+** or Bun | Panel frontend; with `--yes` + privileges, best-effort apt/brew/winget |
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
| Update | `bash deploy/hestiacp/update.sh` (role=panel sparse-checkout; excludes `worker/`) |
| Daily auto-update | systemd `scrapeboard-auto-update.timer` (check git → full update when behind; `AUTO_UPDATE_*` in `deploy/config.env`) |


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
Role first:
  user (Telegram, default) → Telegram ID required; optional display name + package
  admin (panel login)      → username, email, temp password required
```

Telegram users get opaque internal username/email/password (not shown).  
Use **Assign package** / **Change plan** on a user row to grant a subscription (same as Billing → Grant).  
Admins must change password and enable 2FA on first login.

### 3. Packages & billing (Admin → Packages / Billing)

1. Create packages (slug, price USDT, days, threads, upload MB, tier)  
2. **Billing**:
   - Enable billing  
   - USDT TRC-20: receiving wallet (+ optional TronScan API key) — `/paid <txid>` auto-verify  
   - USDT BEP-20 (BNB Smart Chain): receiving `0x…` wallet — bot sends QR + details; admin `/approve`  
   - Manual methods JSON, e.g.:

```json
[
  {"name": "Bank Transfer", "details": "Bank X, IBAN YY, Ref: your user id"}
]
```

3. **Grant** a package to a user, or let them **Buy** (`/buy <slug> [trc20|bep20]`) + `/paid` / panel TxID verify / admin approve  
4. **Pending orders** → Approve (manual / BEP-20 payments)  

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
2. Optionally seed scrape flags from a **package** (or use built-in defaults)  
3. Click **Settings** to edit per-worker scrape flags (engine, threads, delays, headless, …)  
4. Lease merge order: **package scrape defaults → worker overrides → job settings**; captcha is global  
5. Flags are sent in every lease and synced into the agent’s local `worker_config.json` (`scrape` key) on heartbeat  
6. Captcha solvers come from **Admin → Captcha** (global), not per worker/package  
7. Optional: drain / disable / rotate token  

Install hint (panel shows this when creating a worker):

```bash
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token TOKEN
# then (default):
#   macOS/Linux: bash install_service.sh
#   Windows:     install_service.bat
```

### 6. Packages & 2captcha / CaptchaAI (Admin)

- **Packages** — subscription limits **and** default scrape flags (engine, delays, chunk size, …). Applied as the lease base layer for subscribers on that package.  
- **Workers** — per-machine overrides on top of package defaults.  
- **2captcha / CaptchaAI** — global primary + backup solvers. Applied to all job leases.  

### 7. Bot Builder (Admin → Bot builder)

See [Telegram Bot Builder](#telegram-bot-builder).

### 8. User flow (Jobs)

1. Active subscription (or admin)  
2. **Jobs → New job** → pick scraper → upload inputs (`keywords`/`locations`, or `emails` / Google dorks)  
3. Optional engine/threads/dork/validate flags (threads ≤ plan allowance)  
4. **One job at a time:** only one job per owner runs; additional jobs stay **queued** until the running one finishes (completes / stops / fails). Thread allowance still caps threads on that single job.  
5. Watch progress; **Stop** or wait for complete → **Download** ZIP  

Telegram: `/help` · `/scrapers` · `/run source=…` · `/support` — [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md).  
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
3. Runs the scraper for that chunk’s `source` (Maps, Search, email, social, …)  
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
| `/start` `/help` `/scrapers` | everyone / gated | Welcome; help + user guide attachment; allowed scrapers |
| `/whoami` | everyone | Telegram id + link status |
| `/packages` | everyone* | List plans |
| `/buy <slug>` | linked users | Create order + payment instructions |
| `/paid <txid>` | linked users | On-chain USDT TRC-20 verify (BEP-20: tip for admin) |
| `/subscription` | users | Own plan |
| `/run [k=v…]` | subscribers | Queue job (`source=`, `use_dork=`, …) |
| `/status` | users | **Own jobs only** |
| `/stop` | subscribers | Stop own job + partial ZIP |
| `/support …` | users | Open/follow-up ticket → support chat; admin replies notify user here |
| `/admin` | admins** | Admin menu + keyboard |
| `/users` `/userinfo` `/adduser` `/deluser` … | admins** | User CRUD |
| `/subs` `/grant` `/revoke` `/extend` | admins** | Subscriptions |
| `/pending` `/approve` `/reject` | admins** | Orders |
| `/workers` `/worker` `/addworker` `/workertoken` … | admins** | Workers (tokens DM’d privately) |
| `/adminpkgs` `/addpkg` `/editpkg` | admins** | Packages |
| `/alljobs` `/job` `/adminstop` | admins** | Cross-user jobs |
| `/tickets` `/ticket` `/reply` `/close` | admins** | Support tickets (user notified on reply/close) |
| `/proxies` `/captcha` `/botstatus` | admins** | Infra / settings (keys stay in panel) |

\* if “public packages” enabled  
\*\* only if “admin Telegram commands” enabled; runtime also requires `User.role=admin` linked via `telegram_id`. Full list: [`panel/README.md`](panel/README.md#telegram-admin).  

**End-user instructions (all modules):** [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md)

### Support tickets

1. **Bot Builder**: enable Support + set support chat id (admin Telegram id or group).
2. **User**: `/support help me with billing` → ticket created (or follow-up on open ticket) → message to support chat.
3. **Admin (Telegram)**: `/tickets` · `/ticket 12` · `/reply 12 …` · `/close 12` — or reply in-thread to the forwarded `Support #12` message.
4. **Admin (panel)**: **Admin → Support** → open ticket → Reply / Close.
5. User receives reply/close notices **instantly on Telegram**. Closed tickets stay closed; `/support` again opens a new ticket.

### Upload inputs in Telegram

Send a `.txt` / `.csv` (UTF-8) document with caption:

| Caption | Role |
|---------|------|
| `keywords` or `dork` | Queries / niches / dork lines |
| `locations` or `region` | Cities or regions (keyword × location jobs) |
| `emails` | Email list for `source=email_validate` |

- **TXT:** one entry per line; blank lines and `#` comments ignored. Locations: `city,state,country`.
- **CSV:** same line format, or a header column named `keyword`/`query`, `location`, or `email`.
- **`email_validate`:** emails file only (no locations).
- **`google_search` + `use_dork=yes`:** keywords = full Google queries; locations optional.
- Invalid/empty/wrong-type files are rejected **before** a job is queued. See `/help` (user guide attached).

Then queue, for example:

```text
/run source=gmaps engine=chrome threads=2
/run source=google_search use_dork=yes
/run source=email_validate
/run source=tiktok_shop
```

Your allowed modules: `/scrapers`. Full guide: [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md).

### Link a Telegram user

Admin → Users → set **Telegram ID** (user can get it via `/whoami` on the bot).

---

## Scraper modules

Jobs carry a `source` field (default **`gmaps`**). Site-wide enable flags live under **Admin → Scrapers**; packages have **allowed sources**. Workers dispatch by `source` in `agent.py`.

| `source=` | Module | Inputs |
|-----------|--------|--------|
| `gmaps` | Google Maps businesses | keywords × locations; optional `scrape_websites=yes\|no` (email/social from sites) |
| `tiktok_shop` | TikTok Shop creators | niches × regions |
| `google_search` | Google SERP (+ optional dorks) | keywords × locations, or dork lines |
| `email_harvest` | Emails via Google Search channel | keywords × locations |
| `email_validate` | Syntax / disposable / MX (+ optional SMTP) | email list |
| `facebook_pages` / `facebook_groups` / `facebook_posts` / `facebook_comments` | Meta public discovery | keywords × locations |
| `instagram` | Instagram discovery | keywords × locations |
| `tiktok` | General TikTok profiles | keywords × locations |
| `youtube` / `reddit` / `pinterest` | Public social search | keywords × locations |
| `linkedin` / `twitter` | SERP discovery (high risk) | keywords × locations |

Telegram: `/scrapers` · Panel: Jobs scraper picker · Details: [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md) · Maps engine flags: [`worker/SCRAPER.md`](worker/SCRAPER.md).

---

## Billing & subscriptions

### Package fields

- `slug`, `name`, `tier`, `price_usdt`, `duration_days`  
- `threads`, `max_upload_mb`, `allowed_engines`, `is_active`  

### Rules

- Active subscription required to run jobs (admins bypass)  
- **Upgrade-only** while subscribed (same or higher tier)  
- Thread and upload caps enforced from package + user perms  
- USDT TRC-20: verify recipient, contract, amount, confirmation; TxID single-use
- USDT BEP-20: QR (EIP-681) + wallet details; admin approve (no auto on-chain verify yet)  
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

Lease settings merge order for each chunk:

1. **Package** `scrape_defaults` (job owner’s subscription package)  
2. **Worker** `worker_config` (Admin → Workers machine overrides)  
3. **Job** settings (engine / threads / websites / max_results from create/edit)  
4. **Global captcha** (Admin → 2captcha / CaptchaAI) — always wins for `captcha_*` keys  

### Create a job (panel)

`POST /api/jobs` multipart:

- `keywords` file  
- `locations` file  
- optional `engine`, `threads`, `scrape_websites`, `max_results`  

`threads` for a single job cannot exceed the user’s allowance (`min(perms.max_threads, subscription.threads)`).  
At most **one job per owner** may be `running` (or hold leased chunks) at a time. Extra jobs remain `queued` until that job finishes; workers then promote the next queued job in order.

`GET /api/jobs/quota` → `{ thread_allowance, threads_in_use, threads_free }`  
`PATCH /api/jobs/{id}` (queued only) → `{ threads?, engine? }` to adjust threads on a waiting job.

### Job statuses

`queued` → `running` → `completed` | `stopped` | `failed`

Queued jobs may show **1 job at a time — waiting for job X to finish** when another job for the same owner is active.

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
/api/settings/captcha
/api/settings/security
/api/bot/settings
/api/bot/commands
/api/bot/workflows
/api/bot/install-demos
/api/bot/restart
/api/stats/live                 # dashboard fleet + job aggregates (admin sees system)
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

Each host has a durable **machine role** in `.scrapeboard-role` (`panel` or `worker`, gitignored) set on first install. Override with `SCRAPEBOARD_ROLE=panel|worker`. Updates refuse to run the wrong role’s sync (use `--force-role` / `FORCE_ROLE_SWITCH=1` only when intentionally reconfiguring).

| Role | Sparse-checkout | Update command |
|------|-----------------|----------------|
| **panel** | Keep all except `worker/` | `bash deploy/hestiacp/update.sh` (daily: `auto_update.sh` / systemd timer) |
| **worker** | Keep root install helpers + `worker/`; exclude `panel/` and `deploy/` | `python3 install.py --role worker --update` or `bash worker/update.sh` (daily: `--auto-update` via `install_service.*`) |

```bash
# Panel VPS — sync code, then as root:
bash /home/cvmso/apps/scrapeboard/deploy/hestiacp/update.sh
```

Update **keeps** existing `panel/backend/.env` (secrets preserved), rebuilds frontend, restarts systemd, refreshes nginx snippet. Never pulls `worker/` onto the panel host.

**Workers** (each scrape machine):

```bash
# From repo root (preferred — applies worker sparse-checkout, then pip):
python3 install.py --role worker --update
# or: bash worker/update.sh          # Windows: worker\update.bat

# Then restart the service (config + token stays in worker_config.json):
bash worker/install_service.sh       # Windows: worker\install_service.bat
```

Or uninstall → update → reinstall: `bash worker/install_service.sh --uninstall` then update, then `bash worker/install_service.sh`.

**Fleet update from the panel:** after pushing to GitHub, Admin → Workers → **Update all workers** (or per-row **Request update**). Online agents pull via heartbeat and restart — see `worker/README.md` (one-click fleet update).

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
