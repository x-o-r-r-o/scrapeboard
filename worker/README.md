# Scrapeboard Worker

Scrape-only agent for **Windows, macOS, and Linux**. Connects to the Scrapeboard panel, leases job chunks, scrapes with `gmaps_scraper.py`, and uploads ZIP results.

**This folder has no Telegram, billing, or user UI** — those live in the [panel](../panel/README.md).

Production panel URL: **`https://scrape.cvmso.com`**

---

## Requirements

| | |
|--|--|
| OS | Windows 10+, macOS 12+, or modern Linux |
| Python | **3.10+** (`python3` / Windows: `py -3`) |
| Network | Outbound HTTPS to the panel (no inbound ports) |
| Panel | Admin → Workers → create worker → **copy token once** |

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

1. **Panel URL** — default `https://scrape.cvmso.com`  
2. **Worker token** — from Admin → Workers (shown once)  
3. **Worker name** — hostname by default  
4. **Default engine** — e.g. `chrome`, `brave`, `camoufox` (selftest + first bootstrap)  

Config file: **`worker_config.json`** (gitignored — contains the token).

On each heartbeat the panel pushes effective **worker settings** into this file under `scrape` (engine, threads, delays, headless, captcha, …). Job leases include the same merged settings so scrapes match **Admin → Workers**.

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

```bash
# After wizard / config exists:
python agent.py

# Or one-shot (no config file):
python agent.py --panel-url https://scrape.cvmso.com --token YOUR_TOKEN

# Local panel during development:
python agent.py --panel-url http://127.0.0.1:3010 --token YOUR_TOKEN
```

On start the agent calls `/api/worker-api/hello` to verify the token, then heartbeats and leases work only for **jobs created by panel users or linked Telegram accounts**. Keep the token secret; rotate it in Admin → Workers if leaked.

Keep it running as a **background service** (recommended — see below), or leave a terminal open / use `tmux`.

---

## Install as background service

Installs a login/boot job that runs `python agent.py --service`: logs to `logs/worker.log`, uses a stable `work/` directory, keeps leasing panel jobs, and restarts if the process exits. Requires `worker_config.json` (run the wizard first).

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

## CLI reference

```text
python agent.py                         # wizard if no config; else run
python agent.py --setup                 # re-run wizard
python agent.py --panel-url URL --token TOKEN [--name NAME]
python agent.py --config PATH           # alternate config file
python agent.py --work-dir PATH         # scratch dir for chunks
python agent.py --service               # background service paths + file logging
python agent.py --log-file PATH         # redirect logs (file logging)
python agent.py --selftest [--engine E]
python agent.py --force-setup | --skip-setup
```

Panel install hint (shown when creating/rotating a worker):

```text
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token <TOKEN>

# After config exists, install as a background service (starts at login):
#   macOS/Linux:  bash install_service.sh
#   Windows:      install_service.bat
```

---

## What the agent does

1. `POST /api/worker-api/heartbeat` — online + CPU/RAM/disk/load + host identity  
2. `POST /api/worker-api/lease` — up to **`max_browsers` concurrent leases** (one instance per user job chunk); each lease includes keywords/locations + merged settings + proxies  
3. Runs `gmaps_scraper` for that chunk using the job’s **thread** count (browsers inside the instance)  
4. Zips CSV parts → `POST /api/worker-api/upload`  
5. `POST /api/worker-api/ack` — panel merges when all chunks complete → user ZIP (+ optional Telegram)  

**Panel-side thread quota:** the panel only promotes a user’s queued job when the sum of that user’s running job threads stays within their plan allowance. Unassigned users share the worker pool; dedicated-worker packages may optionally pin workers.

Work directories are isolated per user: `work_root/user_{owner_id}/{job_id}/`.

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
| Token rejected | Rotate token in panel → `python agent.py --setup` |
| Worker stays offline | Check URL (`https://scrape.cvmso.com`), outbound HTTPS, firewall |
| Brave auto-install fails | Install from brave.com or use engine `chrome` |
| Playwright deps (Linux) | `python -m playwright install-deps chromium` (may need sudo) |
| Gatekeeper (macOS) | Right-click `setup_and_run.command` → Open |
| Service not starting | Check `logs/worker.log`; confirm `worker_config.json` exists; re-run `install_service.*` |
| Want clean reinstall | Delete `.venv`, `worker_config.json`, re-run setup script |

---

## Related docs

- Project overview: [`../README.md`](../README.md)  
- Panel: [`../panel/README.md`](../panel/README.md)  
- Deploy: [`../deploy/hestiacp/README.md`](../deploy/hestiacp/README.md)  
