# Google Maps Scraper — Scrapeboard (Panel) + Worker

| Folder | Role |
|--------|------|
| [`panel/`](panel/) | Scrapeboard web control panel (FastAPI + React) |
| [`worker/`](worker/) | Worker-only scrape agent |
| [`deploy/`](deploy/) | HestiaCP production install (OpsBoard-style) |

**Production:** deploy Scrapeboard once on HestiaCP (`scrape.cvmso.com`, systemd, port **3010**).  
Workers on other machines: `--panel-url https://scrape.cvmso.com`.

See [deploy/hestiacp/README.md](deploy/hestiacp/README.md), [panel/README.md](panel/README.md), [worker/README.md](worker/README.md).
