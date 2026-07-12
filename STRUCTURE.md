# Google Maps Scraper — Scrapeboard (Panel) + Worker

| Folder | Role |
|--------|------|
| [`panel/`](panel/) | Scrapeboard web control panel (FastAPI + React) — [README](panel/README.md) |
| [`worker/`](worker/) | Worker-only scrape agent (Windows / macOS / Linux) — [README](worker/README.md) |
| [`deploy/`](deploy/) | HestiaCP production install (OpsBoard-style) — [README](deploy/hestiacp/README.md) |

**Production:** deploy Scrapeboard once on HestiaCP (`scrape.cvmso.com`, systemd, port **3010**). Panel install/update **exclude `worker/`** (sparse-checkout); scrape agents are installed separately on worker hosts.

**Workers:** `setup_and_run.*` → wizard → **`install_service.*`** (default background service) → panel URL `https://scrape.cvmso.com`. Optional Tailscale via wizard / `tailscale_enabled` in `worker_config.json` (default off).

**Start here:** `./install.sh` / `install.bat` / `python3 install.py` (panel vs worker by OS) — [README.md → Run by default](README.md#run-by-default).
