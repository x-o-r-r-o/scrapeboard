# Scrapeboard on HestiaCP — full deploy guide

Production install for a HestiaCP VPS (same model as OpsBoard / OmniDesk):

| Piece | How it runs |
|-------|-------------|
| Frontend | Static React build in Hestia `public_html` |
| Backend | **systemd** `scrapeboard` → FastAPI on `127.0.0.1:3010` |
| Nginx | Hestia snippet proxies `/api/` → the API |
| Workers | Separate machines; HTTPS to this panel only (`worker/` is **not** installed on the panel host) |

```
Browser / Telegram / Workers
        │
        ▼
https://scrape.cvmso.com
   ├── /        → /home/cvmso/web/scrape.cvmso.com/public_html
   └── /api/*   → 127.0.0.1:3010  (scrapeboard.service)
```

| App on same VPS | Port |
|-----------------|------|
| OpsBoard | 3000 |
| OmniDesk | 3001 |
| **Scrapeboard** | **3010** |

---

## Defaults (this project)

| Setting | Value |
|---------|-------|
| Hestia user | `cvmso` |
| Domain | `scrape.cvmso.com` |
| App directory | `/home/cvmso/apps/scrapeboard` |
| Public HTML | `/home/cvmso/web/scrape.cvmso.com/public_html` |
| API bind | `127.0.0.1:3010` |
| systemd unit | `scrapeboard.service` |
| Nginx snippet | `nginx.ssl.conf_scrapeboard` |
| GitHub repo | `https://github.com/x-o-r-r-o/scrapeboard.git` |

---

## 0. Prerequisites

On your laptop / DNS:

1. Point **scrape.cvmso.com** A-record → VPS public IP.
2. You can SSH as **root**: `ssh root@YOUR_SERVER_IP`
3. HestiaCP is installed; system user **cvmso** exists.

---

## 1. HestiaCP UI (once)

1. Log in to HestiaCP.
2. **WEB → Add Web Domain** → `scrape.cvmso.com`
3. Enable **SSL / Let's Encrypt**.
4. Confirm `https://scrape.cvmso.com` shows the default Hestia page (or empty site) with a valid cert.

CLI equivalent (optional, as root):

```bash
v-add-web-domain cvmso scrape.cvmso.com
v-add-letsencrypt-domain cvmso scrape.cvmso.com
v-rebuild-web-domain cvmso scrape.cvmso.com
```

---

## 2. Install Scrapeboard (once, as root)

### 2a. Clone from GitHub

```bash
ssh root@YOUR_SERVER_IP

mkdir -p /home/cvmso/apps
cd /home/cvmso/apps

# If the folder does not exist yet (full clone is fine — install.sh will
# sparse-checkout and drop worker/ so the panel never keeps scrape runtime):
git clone https://github.com/x-o-r-r-o/scrapeboard.git scrapeboard

# Fix ownership (avoids "dubious ownership" when root installs)
chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard
git config --global --add safe.directory /home/cvmso/apps/scrapeboard

cd /home/cvmso/apps/scrapeboard
```

**Panel vs worker:** the control panel only needs `panel/`, `deploy/`, and top-level docs. Install/update configure **git sparse-checkout** to exclude `worker/` and remove it if present. Scrape agents are installed separately from the repo’s `worker/` folder on worker hosts — never required on the Hestia panel VPS. Local development clones may keep the full repo including `worker/`.

### 2b. Create `deploy/config.env`

```bash
cp deploy/config.env.example deploy/config.env
nano deploy/config.env
```

Example (edit the password):

```bash
CONTROL_PANEL=hestiacp
SITE_USER=cvmso
HESTIA_USER=cvmso
DOMAIN=scrape.cvmso.com
API_PORT=3010
APP_DIR=/home/cvmso/apps/scrapeboard

BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_EMAIL=admin@cvmso.com
# Use single quotes. Prefer avoiding # in the password, or keep it —
# the installer writes quoted values into panel/backend/.env so # is safe there.
BOOTSTRAP_ADMIN_PASSWORD='YourStrongPasswordHere'

SECRET_KEY=
REPO_URL=https://github.com/x-o-r-r-o/scrapeboard.git
```

**Important:** Unquoted `#` in a `.env` file starts a comment. The installer **quotes** secrets when writing `panel/backend/.env`. If you edit `.env` by hand, always use double quotes around passwords that contain `#`, spaces, or `&`.

### 2c. Run the installer

```bash
cd /home/cvmso/apps/scrapeboard
bash deploy/hestiacp/install.sh
```

What it does:

1. Installs system packages (Python, git, rsync, …)
2. Installs **Bun** (frontend build only)
3. Syncs repo (`git pull` as `cvmso` when `REPO_URL` is set) with **sparse-checkout excluding `worker/`**, then removes `worker/` if still present
4. Writes quoted `panel/backend/.env` (`ENVIRONMENT=production`)
5. Creates Python venv + installs API requirements
6. Builds React → rsync into `public_html`
7. Enables **systemd `scrapeboard`**
8. Installs nginx snippet + `v-rebuild-web-domain cvmso scrape.cvmso.com`
9. Health-checks `http://127.0.0.1:3010/api/health`

### 2d. Alternate: rsync from your Mac (instead of clone)

```bash
# On your Mac, from the project root:
ssh root@YOUR_SERVER_IP 'mkdir -p /home/cvmso/apps'

# Exclude worker/ — scrape agents belong on separate hosts
rsync -az --delete \
  --exclude node_modules --exclude .venv --exclude panel/data \
  --exclude '__pycache__' --exclude .git --exclude dist \
  --exclude worker \
  ./ root@YOUR_SERVER_IP:/home/cvmso/apps/scrapeboard/

ssh root@YOUR_SERVER_IP
chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard
cd /home/cvmso/apps/scrapeboard
cp deploy/config.env.example deploy/config.env
# edit config.env, then:
bash deploy/hestiacp/install.sh
```

If you rsync (no `.git`), leave `REPO_URL=` empty in `config.env` or the installer will try to clone/pull. Even with a full rsync, `install.sh` / `update.sh` delete `worker/` from `APP_DIR` if it appears.

---

## 3. First login

1. Open **https://scrape.cvmso.com**
2. Username: `admin` (or `BOOTSTRAP_ADMIN_USERNAME`)
3. Password: the value from `deploy/config.env`
4. Change password → enable **TOTP 2FA**

Verify API:

```bash
curl -s http://127.0.0.1:3010/api/health
# {"ok":true}

systemctl status scrapeboard
journalctl -u scrapeboard -n 50 --no-pager
```

### If login says invalid (password with `#` truncated on an older install)

```bash
cd /home/cvmso/apps/scrapeboard
sudo -u cvmso git pull --ff-only

# Reset the password stored in the DB (admin already exists — bootstrap will not recreate it)
bash deploy/hestiacp/reset_admin_password.sh 'YourNewStrongPassword'

systemctl restart scrapeboard
```

Then log in with the new password.

---

## 4. Service management

```bash
systemctl status scrapeboard
systemctl restart scrapeboard
systemctl stop scrapeboard
systemctl start scrapeboard
journalctl -u scrapeboard -f
```

Disable / remove service:

```bash
systemctl disable --now scrapeboard
rm -f /etc/systemd/system/scrapeboard.service
systemctl daemon-reload
# optional: remove nginx snippet and rebuild domain
rm -f /home/cvmso/conf/web/scrape.cvmso.com/nginx.ssl.conf_scrapeboard
v-rebuild-web-domain cvmso scrape.cvmso.com
```

---

## 5. Update after code changes

```bash
ssh root@YOUR_SERVER_IP
cd /home/cvmso/apps/scrapeboard
chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard
# Prefer update.sh (re-applies sparse-checkout, drops worker/, rebuilds, restarts).
# A bare git pull is OK after the first install/update has enabled sparse-checkout.
bash deploy/hestiacp/update.sh
```

Or from Mac after local commits:

```bash
rsync -az --delete \
  --exclude node_modules --exclude .venv --exclude panel/data \
  --exclude '__pycache__' --exclude .git --exclude dist \
  --exclude worker \
  ./ root@YOUR_SERVER_IP:/home/cvmso/apps/scrapeboard/

ssh root@YOUR_SERVER_IP 'bash /home/cvmso/apps/scrapeboard/deploy/hestiacp/update.sh'
```

**Update keeps** existing `panel/backend/.env` (secrets are not overwritten).

---

## 6. Paths reference

| Item | Path |
|------|------|
| App | `/home/cvmso/apps/scrapeboard` |
| Deploy config | `/home/cvmso/apps/scrapeboard/deploy/config.env` |
| API env | `/home/cvmso/apps/scrapeboard/panel/backend/.env` |
| DB / uploads / results | `/home/cvmso/apps/scrapeboard/panel/data/` |
| Public site | `/home/cvmso/web/scrape.cvmso.com/public_html/` |
| Nginx snippet | `/home/cvmso/conf/web/scrape.cvmso.com/nginx.ssl.conf_scrapeboard` |
| systemd unit | `/etc/systemd/system/scrapeboard.service` |

---

## 7. Nginx note

The custom snippet **only** adds `location /api/` and SPA `error_page 404`.  
**Do not** add a second `location /` — Hestia already owns `/`; duplicates break nginx.

```bash
nginx -t
v-rebuild-web-domain cvmso scrape.cvmso.com
```

---

## 8. After panel is up — workers

Panel servers **never need** `worker/` in `APP_DIR`. On each scrape host, clone/copy the repo’s `worker/` folder (or the full repo for development) and run setup there.

1. Panel → **Admin → Workers → Create** → copy token once.
2. On the worker machine (Windows / macOS / Linux):

```bash
# Copy or clone only what you need on the scrape host, e.g.:
#   git clone https://github.com/x-o-r-r-o/scrapeboard.git && cd scrapeboard/worker
cd worker
# Windows: setup_and_run.bat
# macOS:   open setup_and_run.command   or  bash setup_and_run.sh
# Linux:   bash setup_and_run.sh

# Or:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py --setup
# Panel URL: https://scrape.cvmso.com
# Token:     (from panel)
# Optional Tailscale: see worker/README.md (default off)

# Default: install as background service (starts at login, keeps leasing jobs)
#   macOS/Linux:  bash install_service.sh
#   Windows:      install_service.bat
# Linux linger (after logout / at boot):  sudo loginctl enable-linger "$USER"
```

Workers only process jobs created by **panel users** or **linked Telegram** accounts.  
Day-to-day status/logs/uninstall: [`../../worker/README.md`](../../worker/README.md).

---

## 404 on /login (React Router)

Hestia serves files from `public_html`. Paths like `/login` are not real files, so nginx/Apache must fall back to `index.html`.

After pulling this fix:

```bash
cd /home/cvmso/apps/scrapeboard
sudo -u cvmso git pull --ff-only
bash deploy/hestiacp/update.sh
```

Then either:

1. HestiaCP → **WEB** → edit `scrape.cvmso.com` → set **Nginx template** to `scrapeboard` → Save, or  
2. Confirm the update script applied it via `v-change-web-domain-tpl`.

Quick checks:

```bash
ls -la /home/cvmso/web/scrape.cvmso.com/public_html/index.html
ls -la /home/cvmso/web/scrape.cvmso.com/public_html/.htaccess
curl -sI https://scrape.cvmso.com/login | head -5
# Expect HTTP/2 200 (not 404)
curl -s http://127.0.0.1:3010/api/health
```

---

| Problem | Commands / fix |
|---------|----------------|
| `dubious ownership` | `chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard` then `git config --global --add safe.directory /home/cvmso/apps/scrapeboard` |
| Blank SPA / 404 on `/login` | Rebuild frontend + SPA fallback: `bash deploy/hestiacp/update.sh`. Or HestiaCP → WEB → edit domain → Nginx template **scrapeboard**. Confirm `ls /home/cvmso/web/scrape.cvmso.com/public_html/index.html` |
| Invalid first login | Password truncated at `#` in old unquoted `.env` → `bash deploy/hestiacp/reset_admin_password.sh 'NewPass'` |
| 502 on `/api` | `systemctl status scrapeboard`; `journalctl -u scrapeboard -n 100 --no-pager` |
| Blank SPA | `bash deploy/hestiacp/update.sh` |
| Port in use | Change `API_PORT` in `deploy/config.env`, re-run update |
| Permission errors | `chown -R cvmso:cvmso /home/cvmso/apps/scrapeboard` |
| nginx fail | Remove duplicate `location /`; `nginx -t` |
| Production secret refused | Set a long unique `SECRET_KEY` and strong bootstrap password in `.env` / `config.env` |

Check quoted password in API env:

```bash
grep '^BOOTSTRAP_ADMIN_PASSWORD=' /home/cvmso/apps/scrapeboard/panel/backend/.env
# Should look like: BOOTSTRAP_ADMIN_PASSWORD="...."
```

---

## 10. Script index

| Script | Purpose |
|--------|---------|
| `deploy/hestiacp/install.sh` | Full first-time install (as root) |
| `deploy/hestiacp/update.sh` | Pull/build/restart; keeps `.env` |
| `deploy/hestiacp/reset_admin_password.sh` | Reset admin password in DB |
| `deploy/lib/common.sh` | Shared helpers (`env_quote`, systemd, nginx, …) |
| `deploy/config.env.example` | Template → copy to `config.env` |
| `deploy/hestiacp/nginx.ssl.conf_scrapeboard` | `/api/` proxy snippet |
| `panel/run.sh` | Local API start (`uvicorn` :3010) |
| `worker/setup_and_run.*` | Worker first-run wizard |
| `worker/install_service.*` | Worker background service (default) |

Project overview: [`../../README.md`](../../README.md)
