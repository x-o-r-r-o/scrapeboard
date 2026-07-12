# Scrapeboard — Control Panel

FastAPI + React control panel. Source of truth for users, security, billing, proxy pools, workers, jobs, and Telegram Bot Builder.

Workers are **not** part of this folder — see [`../worker/README.md`](../worker/README.md).  
Production install — see [`../deploy/hestiacp/README.md`](../deploy/hestiacp/README.md).

| | |
|--|--|
| Production URL | `https://scrape.cvmso.com` |
| Hestia user | `cvmso` |
| API bind | `127.0.0.1:3010` |
| systemd | `scrapeboard.service` |
| Local UI | `http://127.0.0.1:5173` (proxies `/api` → `:3010`) |
| Local API helper | `bash panel/run.sh` / `bash panel/run.sh --reload` |

---

## Run by default

Full stack path (panel + worker + Telegram ops): **[root README → Run by default](../README.md#run-by-default)**.

| Mode | How |
|------|-----|
| **Production** | `bash deploy/hestiacp/install.sh` → systemd `scrapeboard` |
| **Local API** | `bash panel/run.sh --reload` (or manual venv + uvicorn `:3010`) |
| **Local UI** | `cd panel/frontend && npm install && npm run dev` |
| **Telegram** | Starts with the API process once Bot Builder has a token + enabled |

Day-to-day (production):

```bash
systemctl status scrapeboard
journalctl -u scrapeboard -f
systemctl restart scrapeboard
```

DB schema + bootstrap admin are applied on API startup (no separate migrate command).

---

## Layout

```
panel/
├── run.sh                   # local API start (venv + uvicorn :3010)
├── backend/                 # FastAPI
│   ├── app/
│   │   ├── api/             # auth, users, billing, jobs, infra, bot, …
│   │   ├── bot/             # Telegram runtime + demos
│   │   ├── core/            # config, DB, security
│   │   ├── services/        # jobs, billing, notify, bootstrap
│   │   └── main.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # React (Vite)
│   └── src/pages/           # login, setup, user + admin screens
├── data/                    # runtime DB / uploads / results (gitignored)
└── README.md
```

---

## Features

### Auth & security

- **No public registration** — admins create users  
- Mandatory **TOTP 2FA** after first password change  
- Brute-force lockout (configurable)  
- reCAPTCHA **v2 or v3** (one mode at a time) on login  

### Users & roles

| Role | Access |
|------|--------|
| **admin** | Full panel + optional Telegram admin commands |
| **user** | Own dashboard, jobs, subscription only |

### Billing

- Packages (slug, USDT price, days, threads, upload MB, tier)  
- USDT TRC-20 TxID verify + manual methods + admin approve/grant  
- Upgrade-only while subscribed; admins bypass plan checks  

### Infra

- Proxy pools → assigned to workers  
- Worker enrollment tokens, heartbeat (CPU/RAM/disk/load), drain/disable  
- **Per-worker scrape flags** (`worker_config`): engine, threads, delays, headless, stealth, captcha, …  
- Scrape profiles (primary + backup captcha) linked to packages/workers  
- **`max_browsers`** = max concurrent **user-job instances** (leases) on that worker — not a per-job thread cap  

### Jobs

- Upload keywords + locations → queued chunks leased by workers  
- **Shared per-user thread pool:** sum of threads across running jobs ≤ plan/perm allowance; extras wait in queue  
- Edit **queued** job threads/engine to fit free capacity (`PATCH /api/jobs/{id}`)  
- Results stored under `results/user_{id}/{public_id}/`  
- Progress, stop, download merged ZIP; admin storage / purge  
- Ownership enforced (users never see others’ jobs)  
- **Admin:** unique job ID, which worker(s) hold leases, chunk counts; users can **Stop** their own queued/running jobs (admins can stop any); Telegram `/stop` still works for owners with `can_stop`  

### Telegram Bot Builder

- Connect BotFather token, toggle commands/audiences  
- Demo workflows, support chat, optional result delivery  

---

## UI routes

| Path | Who | Purpose |
|------|-----|---------|
| `/login` | public | Sign in |
| `/setup/password` | new user | Force password change |
| `/setup/2fa` | new user | Enable TOTP |
| `/app` | user/admin | Dashboard |
| `/app/jobs` | user/admin | Create / monitor / download jobs |
| `/app/subscription` | user/admin | Buy / paid / current plan |
| `/app/admin/users` | admin | Create & manage users |
| `/app/admin/packages` | admin | Plans |
| `/app/admin/billing` | admin | Wallet, methods, pending, grant |
| `/app/admin/proxies` | admin | Proxy pools |
| `/app/admin/workers` | admin | Workers + per-worker scrape flags |
| `/app/admin/scrape` | admin | Global scrape defaults (seed for new workers) |
| `/app/admin/security` | admin | reCAPTCHA + lockout |
| `/app/admin/bot` | admin | Bot Builder |

---

## Local development

### Prerequisites

- Python 3.10+  
- Node.js 18+ or Bun  

### 1. Backend

```bash
# from repo root:
bash panel/run.sh --reload

# or manually:
cd panel/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# set SECRET_KEY + BOOTSTRAP_ADMIN_PASSWORD
uvicorn app.main:app --reload --host 127.0.0.1 --port 3010
```

Health:

```bash
curl -s http://127.0.0.1:3010/api/health
# {"ok":true}
```

OpenAPI: `http://127.0.0.1:3010/docs`

The Telegram bot runtime starts with this process (configure token in Admin → Bot Builder).

### 2. Frontend

```bash
cd panel/frontend
npm install          # or: bun install
npm run dev          # http://127.0.0.1:5173
```

Vite proxies `/api` → `http://127.0.0.1:3010`.

### Bootstrap admin

| Field | Default (from `.env`) |
|-------|------------------------|
| Username | `admin` |
| Password | `BOOTSTRAP_ADMIN_PASSWORD` |
| After login | Change password → enable 2FA |

---

## Environment (`backend/.env`)

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | JWT signing (required in production) |
| `DATABASE_URL` | Async SQLAlchemy URL (default SQLite under `panel/data/`) |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Session length |
| `BOOTSTRAP_ADMIN_*` | First admin seed (username / password / email) |
| `PUBLIC_URL` | Public site URL (`https://scrape.cvmso.com`) |
| `API_PORT` | Listen port (**3010**) |

Never commit `.env` or `panel/data/`.

---

## Production (HestiaCP)

Preferred path — install once, keep running via systemd:

**Full guide (every command):** [`../deploy/hestiacp/README.md`](../deploy/hestiacp/README.md)

```bash
# on VPS as root, after code is in APP_DIR:
bash deploy/hestiacp/install.sh
# later:
bash deploy/hestiacp/update.sh
# if bootstrap login fails (old unquoted # password):
bash deploy/hestiacp/reset_admin_password.sh 'NewPass'
```

| Setting | Value |
|---------|-------|
| Domain | `scrape.cvmso.com` |
| App dir | `/home/cvmso/apps/scrapeboard` |
| Public HTML | `/home/cvmso/web/scrape.cvmso.com/public_html` |
| API | `127.0.0.1:3010` |
| Unit | `scrapeboard.service` |

Full steps: [`../deploy/hestiacp/README.md`](../deploy/hestiacp/README.md)  
Project overview: [`../README.md`](../README.md)

```bash
systemctl status scrapeboard
journalctl -u scrapeboard -f
systemctl restart scrapeboard
```

---

## First-time admin checklist

1. Sign in → change password → enable 2FA  
2. **Security** — reCAPTCHA mode + lockout  
3. **Packages** + **Billing** — wallet / manual methods  
4. **Proxy pools** — paste proxies  
5. **Workers** — create → copy token → **Settings** (engine, threads, headless, pool, …)  
6. **Scrape settings** — global defaults (new workers inherit these)  
7. **Bot Builder** (optional) — token → enable → **Install / refresh demos**  
8. **Users** — create accounts (optional Telegram ID)  

Worker install hint when creating a worker:

```text
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token <TOKEN>

# Default after config: install as background service
#   macOS/Linux:  bash install_service.sh
#   Windows:      install_service.bat
```

**Default ops loop:** admin creates package/user/worker token → operator installs worker as service → user runs jobs in Telegram or panel → admin monitors panel → user `/stop` or panel Stop.

---

## API overview

Base: `https://scrape.cvmso.com/api` (prod) or `http://127.0.0.1:3010/api` (dev).  
User auth: `Authorization: Bearer <access_token>`.  
Worker auth: `Authorization: Bearer <worker-token>`.

### Auth

```text
GET  /api/auth/public-config
POST /api/auth/login
GET  /api/auth/me
POST /api/auth/change-password
POST /api/auth/2fa/setup
POST /api/auth/2fa/enable
GET  /api/auth/ready
GET  /api/health
```

### Admin / panel modules

```text
/api/users
/api/packages
/api/billing/*
/api/orders/*
/api/subscriptions/*
/api/proxy-pools
/api/workers
/api/settings/scrape
/api/settings/security
/api/bot/*
/api/jobs
```

### Worker protocol

```text
POST /api/worker-api/heartbeat
POST /api/worker-api/lease
POST /api/worker-api/upload?job_id=&chunk_id=
POST /api/worker-api/ack
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| 502 on `/api` | `systemctl status scrapeboard`; check journal |
| Blank SPA | Re-run `deploy/hestiacp/update.sh` |
| Login lockout | Wait window or adjust Admin → Security |
| 2FA codes fail | Sync server NTP (`timedatectl`) |
| CORS errors locally | Ensure `CORS_ORIGINS` includes `http://127.0.0.1:5173` |
| Stale API on wrong port | Use **3010**, not 8000 / 3000 / 3001 |
