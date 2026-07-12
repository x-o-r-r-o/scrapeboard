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

Keep it running:

| OS | Suggestion |
|----|------------|
| Linux | `systemd` user/service unit, or `tmux` / `screen` |
| macOS | `tmux` / `screen`, or LaunchAgent |
| Windows | leave the window open, Task Scheduler, or NSSM service |

---

## CLI reference

```text
python agent.py                         # wizard if no config; else run
python agent.py --setup                 # re-run wizard
python agent.py --panel-url URL --token TOKEN [--name NAME]
python agent.py --config PATH           # alternate config file
python agent.py --work-dir PATH         # scratch dir for chunks
python agent.py --selftest [--engine E]
python agent.py --force-setup | --skip-setup
```

Panel install hint (shown when creating/rotating a worker):

```text
python agent.py --setup
# or:
python agent.py --panel-url https://scrape.cvmso.com --token <TOKEN>
```

---

## What the agent does

1. `POST /api/worker-api/heartbeat` — online + CPU/RAM  
2. `POST /api/worker-api/lease` — chunk + keywords/locations + settings + proxies  
3. Runs `gmaps_scraper` for that chunk  
4. Zips CSV parts → `POST /api/worker-api/upload`  
5. `POST /api/worker-api/ack` — panel merges when all chunks complete → user ZIP (+ optional Telegram)  

---

## Contents

| File | Purpose |
|------|---------|
| `agent.py` | Entrypoint, wizard, panel client |
| `gmaps_scraper.py` | Cross-platform scrape engine |
| `setup_and_run.bat` | Windows first-run |
| `setup_and_run.sh` | Linux/macOS first-run |
| `setup_and_run.command` | macOS Finder launcher |
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
| Want clean reinstall | Delete `.venv`, `worker_config.json`, re-run setup script |

---

## Related docs

- Project overview: [`../README.md`](../README.md)  
- Panel: [`../panel/README.md`](../panel/README.md)  
- Deploy: [`../deploy/hestiacp/README.md`](../deploy/hestiacp/README.md)  
