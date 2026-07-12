# Google Maps Scraper — Scrapeboard (Panel) + Worker

| Folder | Role |
|--------|------|
| [`panel/`](panel/) | Scrapeboard web control panel (FastAPI + React) — [README](panel/README.md) |
| [`worker/`](worker/) | Worker-only scrape agent (Windows / macOS / Linux) — [README](worker/README.md) |
| [`deploy/`](deploy/) | HestiaCP production install (OpsBoard-style) — [README](deploy/hestiacp/README.md) |

**Production:** deploy Scrapeboard once on HestiaCP (`scrape.cvmso.com`, systemd, port **3010**).

**Workers:** `setup_and_run.*` → wizard → **`install_service.*`** (default background service) → panel URL `https://scrape.cvmso.com`.

**Run everything:** [README.md → Run by default](README.md#run-by-default).
