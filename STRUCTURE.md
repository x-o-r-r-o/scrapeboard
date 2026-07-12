# Google Maps Scraper — Scrapeboard (Panel) + Worker

| Folder | Role |
|--------|------|
| [`panel/`](panel/) | Scrapeboard web control panel (FastAPI + React) — [README](panel/README.md) |
| [`worker/`](worker/) | Worker-only scrape agent (Windows / macOS / Linux) — [README](worker/README.md) |
| [`deploy/`](deploy/) | HestiaCP production install (OpsBoard-style) — [README](deploy/hestiacp/README.md) |

**Production:** deploy Scrapeboard once on HestiaCP (`scrape.cvmso.com`, systemd, port **3010**).

**Workers:** first-run wizard / `setup_and_run.*` → `--panel-url https://scrape.cvmso.com`.

Full docs: [README.md](README.md).
