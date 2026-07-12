# Scrapeboard Worker

Scrape-only agent for **Windows, macOS, and Linux**. Connects to the Scrapeboard panel, leases job chunks, scrapes with `gmaps_scraper.py`, uploads results.

**No Telegram, billing, or user management on the worker.**

## First-run setup (like before)

On a bare machine, run **one** of:

| OS | Command |
|----|---------|
| **Windows** | Double-click `setup_and_run.bat` or `setup_and_run.bat` from cmd |
| **macOS** | Double-click `setup_and_run.command` (or `bash setup_and_run.sh`) |
| **Linux** | `bash setup_and_run.sh` |

Or manually:

```bash
cd worker
python3 -m venv .venv          # Windows: py -3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python agent.py                # first run → interactive wizard
```

### Wizard prompts

1. **Panel URL** — default `https://scrape.cvmso.com`  
2. **Worker token** — from Scrapeboard → Admin → Workers (shown once)  
3. **Worker name** — hostname by default  
4. **Default engine** — `chrome` / `brave` / `camoufox` / … (for selftest + first bootstrap)  

Saves `worker_config.json` (gitignored — contains your token).

### Auto browser / package install

Same as the original scraper: on first use of an engine, `ensure_dependencies` installs:

- Python packages (`playwright`, `camoufox`, …)  
- Browser binary (Chromium / Chrome / Edge / Brave / Camoufox)  
- Linux: `playwright install-deps` best-effort  

Sentinel files live in `~/.gmaps_scraper/` (not in the repo).

```bash
python agent.py --selftest --engine chrome   # verify stack, no panel
python agent.py --force-setup                # re-install browsers
python agent.py --setup                      # re-run wizard
python agent.py --skip-setup                 # never auto-install
```

## Day-to-day run

```bash
# After wizard / config exists:
python agent.py

# Or one-shot without config file:
python agent.py --panel-url https://scrape.cvmso.com --token YOUR_TOKEN
```

Keep it running (`tmux` / `screen` / Windows Service / systemd).

## Contents

| File | Purpose |
|------|---------|
| `agent.py` | Worker entrypoint + first-run wizard |
| `gmaps_scraper.py` | Cross-platform scrape engine |
| `setup_and_run.bat` | Windows one-click setup |
| `setup_and_run.sh` | Linux/macOS setup |
| `setup_and_run.command` | macOS Finder launcher |
| `mac_setup_and_test.command` | Legacy Brave scrape smoke test |
| `requirements.txt` | Python deps |
| `SCRAPER.md` | Full engine / flag documentation |

## Proxy pools

Assigned in the **panel** (Admin → Proxy pools → worker). The agent receives proxies in each lease — no local `proxies.txt` required for panel jobs.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `python` not found (Windows) | Install from python.org, tick “Add to PATH” |
| Brave auto-install fails | Install from brave.com or pass engine `chrome` |
| Playwright deps (Linux) | `python -m playwright install-deps chromium` (may need sudo) |
| Gatekeeper (macOS) | Right-click `setup_and_run.command` → Open |
| Token rejected | Rotate token in panel; re-run `--setup` |
