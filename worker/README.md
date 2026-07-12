# Scrapeboard Worker

Scrape-only agent for **Windows, macOS, and Linux**. Connects to the Scrapeboard panel, leases job chunks, scrapes with `gmaps_scraper.py`, and uploads ZIP results.

**This folder has no Telegram, billing, or user UI** — those live in the [panel](../panel/README.md).

Production panel URL: **`https://scrape.cvmso.com`**

---

## Run by default

1. Panel → **Admin → Workers → Create** → **copy token once**.  
2. First run: from repo root `./install.sh` / `install.bat` → **Worker** (saves `.scrapeboard-role=worker`), or `setup_and_run.bat` / `.sh` / `.command` (wizard → `worker_config.json`).  
3. **Install background service** (recommended): `install_service.bat` / `bash install_service.sh` — automatic with `--yes` when config exists.  
4. Leave it running; it heartbeats and leases jobs from the panel.  
5. Later updates: `python3 install.py --role worker --update` or `bash worker/update.sh` (does not pull `panel/`).

**Noninteractive worker:**

```bash
export SCRAPEBOARD_PANEL_URL=https://scrape.example
export SCRAPEBOARD_TOKEN='…'
# Linux/macOS:
bash setup_and_run.sh --yes
# or from repo root:
python3 install.py --role worker --yes
# Windows: setup_and_run.bat /Y   or   install.bat --role worker --yes
```

Full stack (panel + ops): **[root README → Run by default](../README.md#run-by-default)**.

---

## Requirements

| | |
|--|--|
| OS | Windows 10+, macOS 12+, or modern Linux |
| Python | **3.10+** — auto via apt / Homebrew `python@3.12` / winget when possible |
| Linux packages | `python3`, `pip`, `python3.X-venv`; `build-essential` only if needed; Playwright deps best-effort when root/passwordless sudo |
| Network | Outbound HTTPS to the panel (no inbound ports) |
| Panel | Admin → Workers → create worker → **copy token once** (or `SCRAPEBOARD_TOKEN`) |

---

## First-run setup

Same idea as the original scraper: one script (or `python agent.py`) creates a venv, installs deps, optionally selftests, then opens an **interactive wizard** that saves `worker_config.json`.

### One-click / one-command

| OS | How |
|----|-----|
| **Windows** | Double-click `setup_and_run.bat` (or run it from cmd) |
| **macOS** | Double-click `setup_and_run.command`, or `bash setup_and_run.sh` |
| **Linux** | `bash setup_and_run.sh` |

macOS Gatekeeper: right-click → **Open** the first time.

### Manual

```bash
cd worker
python3 -m venv .venv          # Windows: py -3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python agent.py                # first run → wizard
```

### Wizard prompts

1. **Panel URL** — default `https://scrape.cvmso.com` (or `SCRAPEBOARD_PANEL_URL`)  
2. **Worker token** — from Admin → Workers (shown once) (or `SCRAPEBOARD_TOKEN`)  
3. **Worker name** — hostname by default (`SCRAPEBOARD_WORKER_NAME`)  
4. **Default engine** — e.g. `chrome`, `brave`, `camoufox` (selftest + first bootstrap)  
5. **Optional Tailscale** — default **off**; with `--yes` only if `--tailscale` / `SCRAPEBOARD_TAILSCALE=1`  

With `SCRAPEBOARD_ASSUME_YES=1` / `--yes`, the wizard is noninteractive and **requires** `SCRAPEBOARD_TOKEN` (unless `worker_config.json` already exists).

Config file: **`worker_config.json`** (gitignored — contains the token).

On each heartbeat the panel pushes effective **worker settings** into this file under `scrape` (engine, threads, delays, headless, captcha from global Admin → Captcha, …). Job leases include the same merged settings so scrapes match the panel.

---

## Optional Tailscale

Tailscale is **not required**. Workers only need outbound HTTPS to the panel. Enable it when you want a private mesh (admin SSH, multi-host debugging, etc.).

| | |
|--|--|
| Config key | `"tailscale_enabled": true` (alias: `"tailscale": true`) |
| Default | `false` |
| Wizard | Asks during `python agent.py --setup` / `setup_and_run.*`; detects an existing CLI |
| Toggle later | Edit `worker_config.json`, then restart the agent/service |
| On start | If enabled, the agent **checks status** only (no package install on every restart). Missing or logged-out Tailscale is a **warning only** — the lease loop continues |
| Wizard install | Best-effort package install when you answer yes during `--setup` |

### Best-effort install / enable

| OS | What the agent tries | Manual commands |
|----|----------------------|-----------------|
| **Linux** | Official `install.sh`; `systemctl enable --now tailscaled` | `curl -fsSL https://tailscale.com/install.sh \| sh` then `sudo tailscale up` |
| **macOS** | `brew install --cask tailscale` when Homebrew exists | Install from [tailscale.com/download](https://tailscale.com/download) or Homebrew; open **Tailscale** app → Sign in; or `tailscale up` |
| **Windows** | `winget install Tailscale.Tailscale` | Or installer from [tailscale.com/download/windows](https://tailscale.com/download/windows); then `tailscale up` |

**Gap:** `tailscale up` normally needs an **interactive browser login** (and often sudo/admin). The agent never blocks on that — finish login yourself, then restart the worker if you want a clean status line.

```bash
# After enabling in config:
python agent.py --setup          # re-run wizard, or edit worker_config.json
# Confirm:
tailscale status
```

---

## Auto browser / package install

On first use of an engine (or `--selftest` / `--force-setup`), the agent runs the same bootstrap as the original scraper:

- Python packages (`playwright`, `camoufox`, …)  
- Browser binary (Chromium / Chrome / Edge / Brave / Camoufox)  
- Linux: `playwright install-deps` best-effort  

Sentinels live under `~/.gmaps_scraper/` (not in the repo).

```bash
python agent.py --selftest --engine chrome   # verify stack, no panel
python agent.py --force-setup                # re-install browsers/deps
python agent.py --setup                      # re-run wizard
python agent.py --skip-setup                 # never auto-install
```

---

## Day-to-day run

**Default:** background service (below). Foreground only when debugging:

```bash
# After wizard / config exists:
python agent.py

# Or one-shot (no config file):
python agent.py --panel-url https://scrape.cvmso.com --token YOUR_TOKEN

# Local panel during development:
python agent.py --panel-url http://127.0.0.1:3010 --token YOUR_TOKEN
```

On start the agent calls `/api/worker-api/hello` to verify the token, then heartbeats and leases work only for **jobs created by panel users or linked Telegram accounts**. Keep the token secret; rotate it in Admin → Workers if leaked.

---

## Install as background service

**This is the default recommended run mode.** Installs a login/boot job that runs `python agent.py --service`: logs to `logs/worker.log`, uses a stable `work/` directory, keeps leasing panel jobs, and restarts if the process exits. Requires `worker_config.json` (run the wizard first). `setup_and_run.*` offers to install this after the wizard.

### macOS (LaunchAgent)

```bash
cd worker
bash install_service.sh              # install + start (RunAtLoad + KeepAlive)
bash install_service.sh --uninstall  # remove
```

| | |
|--|--|
| Status | `launchctl print gui/$(id -u)/com.scrapeboard.worker \| head` |
| Logs | `logs/worker.log` (also `logs/launchd.out.log` / `logs/launchd.err.log`) |
| Plist | `~/Library/LaunchAgents/com.scrapeboard.worker.plist` |

### Linux (systemd user unit)

```bash
cd worker
bash install_service.sh
bash install_service.sh --uninstall
```

| | |
|--|--|
| Status | `systemctl --user status scrapeboard-worker` |
| Logs | `logs/worker.log` or `journalctl --user -u scrapeboard-worker -f` |
| Unit | `~/.config/systemd/user/scrapeboard-worker.service` |

To keep the worker running after logout / at boot without an interactive session:

```bash
sudo loginctl enable-linger "$USER"
```

(The installer tries this once; if it fails, run the command above.)

### Windows (Task Scheduler)

```bat
cd worker
install_service.bat
install_service.bat --uninstall
```

Or PowerShell: `powershell -ExecutionPolicy Bypass -File install_service.ps1` (`-Uninstall` to remove).

| | |
|--|--|
| Status | Task Scheduler → task **ScrapeboardWorker**, or `schtasks /Query /TN ScrapeboardWorker` |
| Logs | `logs\worker.log` |
| Wrapper | `run_service.cmd` (generated; gitignored) — prefers `.venv\Scripts\pythonw.exe` (no console window) |

Starts **At logon**, restarts on failure. Run the installer as Administrator if creating the task with highest privileges fails (it falls back automatically).

### Service mode flags

```bash
python agent.py --service                 # log → logs/worker.log, work → work/
python agent.py --service --log-file PATH # custom log path
python agent.py --service --work-dir PATH # override work dir
```

Foreground runs (no `--service`) still use a temp work directory unless you set `--work-dir` or `work_dir` in config.

---

## Update / uninstall

Worker hosts should have `.scrapeboard-role=worker` (written by `install.py`). Updates use **worker sparse-checkout** (`/*` minus `panel/` and `deploy/`) so panel sources never land on scrape machines.

### One-click fleet update (control panel)

After you push worker changes to GitHub, you do **not** need to SSH each VPS:

1. Deploy/update the **panel** as usual (so it has the new admin API/UI).
2. Open **Admin → Workers**.
3. Set **Git ref** (`main` by default, or `latest` for current-branch pull, or a tag/SHA).
4. Click **Update all workers**, or **Request update** on a single row.
5. Watch the **Update** column: `pending` → `updating` → `success` / `failed` (message + time via polling).

Online agents pick the command up on the next heartbeat, wait for active scrapes (up to 10 minutes), run the fixed update path (`install.py --role worker --update --ref …`), report status, then exit so LaunchAgent / systemd / Task Scheduler restarts the new code.

**Requirements on each worker host:**

- Git clone of the repo (not a bare rsync copy) with credentials that can `git fetch` from GitHub
- `.scrapeboard-role=worker` and a background service (`install_service.*`)
- Agent version that understands heartbeat `commands: ["update"]` (0.8.0+)

**If the panel still shows agent `0.7.0`:** that build does **not** cancel mid-scrape or report `active_chunks` for lease cleanup. After pull/update you **must restart** the service so Python loads the new `agent.py`:

```bash
# Linux (user systemd):
cd /path/to/bot
python3 install.py --role worker --update
systemctl --user restart scrapeboard-worker
systemctl --user status scrapeboard-worker --no-pager
grep -E 'scrapeboard worker v' worker/logs/worker.log | tail -3
# Expect: v0.8.4+  (0.7.0 = still the old process)
```

Panel Admin → Workers should show **0.8.4+** within ~15s. Until then, a failed ack on `chunk_id=0` (fixed in panel) plus no cancel support can leave **1 instance running** on the dashboard.

Tailscale is **not** required — workers only need outbound HTTPS to the panel.

### Manual update (SSH)

```bash
# From repo root (preferred):
python3 install.py --role worker --update
# optional: python3 install.py --role worker --update --ref main
# or: bash worker/update.sh          # Windows: worker\update.bat

source worker/.venv/bin/activate     # Win: worker\.venv\Scripts\activate
# pip is refreshed by --update when a venv exists

# re-apply service (keeps worker_config.json):
bash worker/install_service.sh       # Windows: worker\install_service.bat
```

If this machine is marked `panel`, worker update refuses — use `deploy/hestiacp/update.sh` on the panel VPS, or reconfigure with `python3 install.py --role worker --force-role --update`.

Uninstall service only (config stays):

```bash
bash install_service.sh --uninstall    # Windows: install_service.bat --uninstall
```

Clean reinstall: delete `.venv` + `worker_config.json`, re-run `setup_and_run.*`, then `install_service.*`.

---

## Worker settings & flags

Operator reference for everything that configures the worker. Sources of truth: `python agent.py --help`, `python install.py --help`, and the wizard that writes `worker_config.json`. Do not invent flags — if a control is panel-only, it is marked below.

Quick start:

```text
python agent.py                         # wizard if no config; else run
python agent.py --setup                 # re-run wizard, then continue
python agent.py --panel-url URL --token TOKEN [--name NAME]
python agent.py --service               # log → logs/worker.log, work → work/
```

### CLI — `python agent.py`

| Flag | Default | Meaning / when to use |
|------|---------|------------------------|
| `-h`, `--help` | — | Print help and exit |
| `--panel-url URL` | `""` (config / wizard) | Panel base URL, e.g. `https://scrape.cvmso.com` |
| `--token TOKEN` | `""` (config / wizard) | Worker token from Admin → Workers (shown once) |
| `--name NAME` | `""` → config / hostname | Display name sent on hello/heartbeat |
| `--work-dir PATH` | `""` → config; else temp (or `work/` with `--service`) | Scratch root for chunk work (`user_{id}/{job_id}/`) |
| `--config PATH` | `worker_config.json` in `worker/` | Alternate config file path |
| `--setup` | off | Re-run first-run wizard (writes config), then continue the agent |
| `--selftest` | off | Verify browser/stealth locally, then exit (no panel required) |
| `--engine ENGINE` | `""` → config / `chrome` | Engine for `--selftest` / first browser bootstrap (`chrome`, `brave`, `edge`, `camoufox`, …) |
| `--skip-setup` | off | Never auto-install browsers/Python deps |
| `--force-setup` | off | Re-run browser/deps install on this start |
| `--service` | off | Service mode: append log to `logs/worker.log`, use stable `work/` (what `install_service.*` runs) |
| `--log-file PATH` | with `--service`: `logs/worker.log` | Redirect stdout/stderr to this file (does not alone force `work/`) |

CLI credentials (`--panel-url` + `--token`) override the config file. `--name`, `--work-dir`, and `--engine` override matching config keys when set.

### Config file — `worker_config.json`

Created by the wizard / `--setup`. Gitignored (contains the token). Heartbeats also **sync** panel scrape flags into `scrape` and refresh `max_browsers` / `worker_name` / `default_engine`.

| Key | Type | Default (wizard) | Effect |
|-----|------|------------------|--------|
| `panel_url` | string | `https://scrape.cvmso.com` | Panel base URL |
| `token` | string | *(required)* | Worker auth token |
| `worker_name` | string | hostname | Name reported to the panel (panel can overwrite on heartbeat) |
| `default_engine` | string | `chrome` | Local selftest / first bootstrap engine; updated from panel `scrape.engine` |
| `work_dir` | string | `""` (auto temp; `work/` in `--service`) | Scratch directory root |
| `skip_setup` | bool | `false` | Same as `--skip-setup` when true |
| `max_browsers` | int | `2` | Max concurrent **job leases** (instances). Overwritten from panel heartbeat |
| `tailscale_enabled` | bool | `false` | If true, agent checks/reminds Tailscale on start (alias: `tailscale`) |
| `resource_guard` | bool | `true` | When true, refuse new leases if host CPU/RAM exceed caps (see below) |
| `resource_cpu_max_pct` | float | `80` | Host CPU % at/above which new leases pause |
| `resource_ram_max_pct` | float | `80` | Host RAM % at/above which new leases pause |
| `resource_cpu_resume_pct` | float | max−10 (`70`) | CPU % at/below which leasing resumes (hysteresis) |
| `resource_ram_resume_pct` | float | max−10 (`70`) | RAM % at/below which leasing resumes (hysteresis) |
| `scrape` | object | `{}` | Panel-pushed scrape flags (engine, threads, delays, …). Filled on heartbeat; used as a local mirror — **leases carry the effective settings** |

There is no local `engines` list key — only `default_engine` (and panel `scrape.engine`). Proxy lists are **not** stored here for panel jobs; the lease includes proxies from the assigned pool.

### Environment variables

Used by the wizard, `setup_and_run.*`, and root `install.py` / `install.sh`. Truthy values: `1`, `true`, `yes`, `y`, `on`.

| Variable | Used by | Effect |
|----------|---------|--------|
| `SCRAPEBOARD_ASSUME_YES` | agent wizard, setup/install | Noninteractive defaults (same as `--yes` / `-y`) |
| `SCRAPEBOARD_PANEL_URL` | wizard | Default / noninteractive panel URL |
| `SCRAPEBOARD_TOKEN` | wizard | **Required** with `--yes` unless config already exists |
| `SCRAPEBOARD_WORKER_NAME` | wizard | Default worker name |
| `SCRAPEBOARD_ENGINE` | wizard (`--yes`) | Default engine (else `chrome`) |
| `SCRAPEBOARD_WORK_DIR` | wizard (`--yes`) | Optional `work_dir` in config |
| `SCRAPEBOARD_TAILSCALE` | wizard, setup/install | Enable Tailscale in config / pass `--tailscale` |
| `SCRAPEBOARD_ROLE` | `install.py`, `update.*` | Override `.scrapeboard-role` (`panel` \| `worker`) |
| `SCRAPEBOARD_RESOURCE_GUARD` | agent runtime | `0`/`false` disables CPU/RAM lease backpressure; `1`/`true` forces on |
| `SCRAPEBOARD_CPU_MAX_PCT` | agent runtime | Host CPU % cap before refusing new leases (default `80`; `≥100` disables CPU axis) |
| `SCRAPEBOARD_RAM_MAX_PCT` | agent runtime | Host RAM % cap before refusing new leases (default `80`; `≥100` disables RAM axis) |
| `SCRAPEBOARD_CPU_RESUME_PCT` | agent runtime | Resume leasing at/below this CPU % (default max−10) |
| `SCRAPEBOARD_RAM_RESUME_PCT` | agent runtime | Resume leasing at/below this RAM % (default max−10) |

Env resource caps override matching `worker_config.json` keys. Set a max to `100` (or higher) to disable that axis only.

### Install / setup / update flags

**Root installer** — `python3 install.py` / `./install.sh` / `install.bat` (see `install.py --help`):

| Flag | Meaning |
|------|---------|
| `--role panel\|worker` | Skip role menu; persist to `.scrapeboard-role` |
| `-y`, `--yes` | Noninteractive after role is set (sets `SCRAPEBOARD_ASSUME_YES`) |
| `--tailscale` | Worker: enable Tailscale in setup (install best-effort; login still manual) |
| `--update` | Sparse git pull for this machine’s role + refresh deps hints |
| `--force-role` | Allow switching `.scrapeboard-role` without confirm |
| `--dry-run` | Print OS, role, paths; exit |

**Worker first-run** — `setup_and_run.sh` / `.command` / `.bat`:

| Flag | Meaning |
|------|---------|
| `--yes` / `-y` (Windows also `/Y`) | Noninteractive: deps + wizard via env + auto service when config exists |
| `--tailscale` | Enable Tailscale in config |

**Background service** — `install_service.sh` / `.bat` / `.ps1`:

| Flag | Meaning |
|------|---------|
| *(none)* | Install + start service running `agent.py --service` |
| `--uninstall` / `-u` (PS: `-Uninstall`) | Remove the service unit/task |

**Worker update** — `bash worker/update.sh` / `worker\update.bat`:

| Flag | Meaning |
|------|---------|
| *(default)* | `install.py --role worker --update` (refuses if role is `panel`) |
| `--ref REF` | Passed through — sync that branch/tag/SHA (`latest` = current-branch pull) |
| `--force-role` | Passed through when reconfiguring role |

### Service mode

`install_service.*` runs: `python agent.py --service`.

| | Foreground | `--service` |
|--|------------|-------------|
| Log | terminal stdout | `logs/worker.log` (override with `--log-file`) |
| Work dir | temp dir unless `--work-dir` / config | `worker/work/` unless `--work-dir` / config |
| Restart | manual | LaunchAgent / systemd user / Task Scheduler KeepAlive |

Paths are under the `worker/` folder. See [Install as background service](#install-as-background-service) for OS-specific status commands.

### Panel → worker (admin UI)

These are **panel-only** (Admin → Workers / Proxy pools / Scrape / Captcha). The agent does not expose CLI flags for them; heartbeat/lease applies them.

| Panel control | Maps to worker-side |
|---------------|---------------------|
| Create / rotate **token** | Paste into wizard / `token` / `SCRAPEBOARD_TOKEN` |
| **Name** | Heartbeat may refresh `worker_name` |
| **max_browsers** | Concurrent leases; synced into config + runtime |
| **Enabled** / **Drain** | Heartbeat `enabled` / `drain` — stop leasing (drain waits for active jobs) |
| **Proxy pool** assignment | Proxies embedded in each lease (not a local file) |
| Per-worker **scrape flags** | Synced into `scrape` + merged after package defaults on each lease |
| Global **Captcha** (Admin → Captcha) | Injected into leases; not configured in `worker_config.json` |
| **Update all / Request update** | Heartbeat `commands: ["update"]` + `update.ref` → fixed `install.py --role worker --update`, then process exit for service restart |

Panel install hint (create/rotate worker):

```text
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token <TOKEN>

# After config exists:
#   macOS/Linux:  bash install_service.sh
#   Windows:      install_service.bat
```

Standalone engine CLI (`gmaps_scraper.py`) is separate — see [`SCRAPER.md`](SCRAPER.md).

---

## What the agent does

1. `POST /api/worker-api/heartbeat` — online + CPU/RAM/disk/load + host identity + `active_chunks` (so the panel can reclaim orphan leases; may include `done_in_chunk` / `rows` for live UI progress); may set `resource_throttling` when the local resource guard is pausing new leases  
2. `POST /api/worker-api/lease` — up to **`max_browsers` concurrent leases** (one instance per user job chunk); while CPU/RAM are over cap the agent **waits and retries** (jobs stay queued — never denied/failed/skipped; see [Resource guard](#resource-guard-agent-084)); each lease includes keywords/locations + merged settings + proxies  
3. Runs `gmaps_scraper` for that chunk using the job’s **thread** count (browsers inside the instance)  
4. While scraping: `POST /api/worker-api/progress` (~every 2s / each search) so Jobs UI shows climbing searches/rows before the chunk finishes  
5. Zips CSV parts → `POST /api/worker-api/upload`  
6. `POST /api/worker-api/ack` — panel merges when all chunks complete → user ZIP (+ optional Telegram); agent retries on failure; ack is source of truth (clears live counters)  

**Panel-side thread quota:** the panel only promotes a user’s queued job when the sum of that user’s running job threads stays within their plan allowance. Unassigned users share the worker pool; dedicated-worker packages may optionally pin workers.

Work directories are isolated per user: `work_root/user_{owner_id}/{job_id}/`.

### Resource guard (agent **0.8.4+**)

The worker already reports host **CPU/RAM** on every heartbeat. From **0.8.4** it also uses those metrics for **backpressure**:

1. When host CPU **or** RAM is at/above the configured max (default **80%**), the agent **stops taking new leases** and logs `[resource] throttling cpu=… ram=…`.
2. In-flight scrapes keep running to completion (no hard-kill on brief spikes).
3. Leasing resumes only after **both** CPU and RAM fall to/below the resume thresholds (default **70%** = max−10 hysteresis) — logs `[resource] resume …`.
4. Heartbeat may include `resource_throttling: true` so the panel can show busy/skip state (ignored by older panels).

Metrics are **host-wide** via `psutil` (same as heartbeat telemetry). Inside Docker/K8s without cgroup-aware limits surfaced to the process, reported % may reflect the **host**, not the container quota — set caps accordingly or use host-level cgroup limits separately.

Tune via `worker_config.json` or env (see tables above). Example:

```bash
export SCRAPEBOARD_CPU_MAX_PCT=75
export SCRAPEBOARD_RAM_MAX_PCT=80
export SCRAPEBOARD_CPU_RESUME_PCT=65
python agent.py --service
```

### Live progress (agent **0.8.3+**)

Older agents only bump panel progress on **ack** (whole chunk finished), so Jobs can sit at `0/N` for a long time. **0.8.3+** reports mid-chunk progress; deploy the panel change and update workers (Admin → Workers → Request update, or `install.py --role worker --update`).

### Troubleshooting: ack 404 / stuck `1/N` instances

- Route is **`POST /api/worker-api/ack`** (registered on the panel under `/api`). A `404` on `chunk=0` was a panel bug: `chunk_id or -1` treated `0` as missing.
- After upload succeeds but ack fails, older agents leave the DB lease `leased`; heartbeat used to refresh that TTL forever. **0.8.1+** reports `active_chunks` and retries ack.
- Agents still on **0.7.0** ignore cancel and lack lease cleanup — restart after update (see above).
- Progress stuck at 0 while scraping: worker is older than **0.8.3**, or panel has not been redeployed with `/worker-api/progress`.
- Worker idle while CPU/RAM high: resource guard pausing new leases (0.8.5+) — check logs for `[resource] pausing`; jobs stay queued (not failed). Lower `max_browsers` / job threads or raise caps if this lasts too long.
---

## Contents

| File | Purpose |
|------|---------|
| `agent.py` | Entrypoint, wizard, panel client |
| `gmaps_scraper.py` | Cross-platform scrape engine |
| `setup_and_run.bat` | Windows first-run (+ optional service install) |
| `setup_and_run.sh` | Linux/macOS first-run (+ optional service install) |
| `setup_and_run.command` | macOS Finder launcher |
| `install_service.sh` | macOS LaunchAgent / Linux systemd user install |
| `install_service.bat` / `.ps1` | Windows Scheduled Task install |
| `mac_setup_and_test.command` | Legacy Brave smoke test |
| `requirements.txt` | Python deps |
| `keywords.txt` / `locations.txt` / `proxies.txt` | Samples / standalone engine use |
| `SCRAPER.md` | Full engine flags & behavior |
| `worker_config.json` | Local secrets (created on setup; not in git) |

---

## Proxy pools

Assigned in the **panel** (Admin → Proxy pools → worker). Each lease includes proxies — no local `proxies.txt` required for panel jobs.

For standalone engine diagnostics you can still use a local list:

```bash
python gmaps_scraper.py --check-proxies --proxies proxies.txt
python gmaps_scraper.py --selftest --engine chrome
python gmaps_scraper.py --diagnose --proxy-index 0 --engine chrome
```

Full engine docs: [`SCRAPER.md`](SCRAPER.md).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `python` / `python3` not found | Install Python 3.10+; on Windows tick “Add to PATH” |
| `ensurepip is not available` / venv failed (Linux) | `sudo apt-get install -y python3.X-venv` (X = your minor version), then re-run `bash setup_and_run.sh` |
| Token rejected | Rotate token in panel → `python agent.py --setup` |
| Worker stays offline | Check URL (`https://scrape.cvmso.com`), outbound HTTPS, firewall |
| Brave auto-install fails | Install from brave.com or use engine `chrome` |
| Playwright deps (Linux) | `python -m playwright install-deps chromium` (may need sudo) |
| Gatekeeper (macOS) | Right-click `setup_and_run.command` → Open |
| Service not starting | Check `logs/worker.log`; confirm `worker_config.json` exists; re-run `install_service.*` |
| Tailscale warning | Optional — set `"tailscale_enabled": false` or finish `tailscale up` / Sign in |
| Want clean reinstall | Delete `.venv`, `worker_config.json`, re-run setup script |

---

## Related docs

- Project overview: [`../README.md`](../README.md)  
- Panel: [`../panel/README.md`](../panel/README.md)  
- Deploy: [`../deploy/hestiacp/README.md`](../deploy/hestiacp/README.md)  
