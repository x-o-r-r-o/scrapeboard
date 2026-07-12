# Scrapeboard on HestiaCP

Production deploy — same model as OpsBoard / OmniDesk:

- Frontend: static files in Hestia `public_html`
- Backend: **systemd** service (stays up until you stop/remove it)
- Nginx: proxies `/api/` → localhost API
- Not meant to be started daily with `uvicorn` / `npm run` by hand

```
Browser → https://scrape.cvmso.com
            ├── /          → public_html (React build)
            └── /api/*     → 127.0.0.1:3010 (Scrapeboard FastAPI)
```

| App | Port |
|-----|------|
| OpsBoard | 3000 |
| OmniDesk | 3001 |
| **Scrapeboard** | **3010** |

Workers on other machines use:

```bash
python agent.py --panel-url https://scrape.cvmso.com --token WORKER_TOKEN
```

---

## Before you start

- HestiaCP VPS, SSH as root
- Domain **scrape.cvmso.com** A-record → server
- Hestia user **cvmso**
- In HestiaCP: **WEB → Add Web Domain** `scrape.cvmso.com` + **SSL (Let's Encrypt)**

---

## Install (once)

### Option A — upload project, then install

```bash
ssh root@YOUR_SERVER_IP

# Upload this repo to the app dir (from your Mac), e.g.:
# rsync -az --exclude node_modules --exclude .venv --exclude panel/data \
#   ./ root@SERVER:/home/cvmso/apps/scrapeboard/

mkdir -p /home/cvmso/apps
# ensure files exist at /home/cvmso/apps/scrapeboard/panel/...

cd /home/cvmso/apps/scrapeboard
cp deploy/config.env.example deploy/config.env
# edit if needed (defaults already match cvmso / scrape.cvmso.com / port 3010)

bash deploy/hestiacp/install.sh
```

### Option B — git clone

Set `REPO_URL` in `deploy/config.env`, then:

```bash
bash deploy/hestiacp/install.sh
```

The script will:

1. Install Python + Bun (frontend build only)
2. Create venv, install API deps
3. Build React → rsync into `public_html`
4. Write `.env`, enable **systemd `scrapeboard`**
5. Install nginx snippet + `v-rebuild-web-domain`
6. Health-check `http://127.0.0.1:3010/api/health`

Open **https://scrape.cvmso.com** — login with bootstrap admin from `.env`, then change password + enable 2FA.

---

## Keep it running

```bash
systemctl status scrapeboard
journalctl -u scrapeboard -f
systemctl restart scrapeboard
```

Disable / remove:

```bash
systemctl disable --now scrapeboard
rm -f /etc/systemd/system/scrapeboard.service
systemctl daemon-reload
# optionally remove nginx snippet + rebuild domain, and delete APP_DIR / public_html contents
```

---

## Update after code changes

```bash
# pull/rsync new code into APP_DIR, then:
bash /home/cvmso/apps/scrapeboard/deploy/hestiacp/update.sh
```

Does not overwrite an existing `panel/backend/.env` (secrets stay).

---

## Paths

| Item | Path |
|------|------|
| App | `/home/cvmso/apps/scrapeboard` |
| Public site | `/home/cvmso/web/scrape.cvmso.com/public_html` |
| API env | `/home/cvmso/apps/scrapeboard/panel/backend/.env` |
| DB / uploads | `/home/cvmso/apps/scrapeboard/panel/data/` |
| Nginx snippet | `/home/cvmso/conf/web/scrape.cvmso.com/nginx.ssl.conf_scrapeboard` |
| Service | `scrapeboard.service` |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| 502 on `/api` | `systemctl status scrapeboard` — API down |
| Blank page | Re-run update (frontend build + rsync) |
| nginx fail | Do **not** add a second `location /`; `nginx -t` |
| Port in use | Change `API_PORT` in `config.env` (not 3000/3001) and re-run update |
| Permission errors | `chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard` |
