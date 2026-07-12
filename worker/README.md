# GMaps Scraper — Worker agent

This folder is **worker-only**. It scrapes job chunks leased from the control panel.

## Contents

| File | Purpose |
|------|---------|
| `agent.py` | Worker entrypoint — heartbeats, leases, ack |
| `gmaps_scraper.py` | Scrape engine |
| `requirements.txt` | Python deps for browsers/scraping |
| `keywords.txt` / `locations.txt` / `proxies.txt` | Local input samples |
| `mac_setup_and_test.command` | macOS one-click setup + Brave test |
| `config.example.json` / `bot_config.example.json` | Legacy reference — panel is source of truth |
| `SCRAPER.md` | Full scraper feature docs |

## Run

1. In the **Scrapeboard** panel, create a worker and copy the token.
2. On this machine:

```bash
cd worker
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python agent.py --panel-url https://scrape.cvmso.com --token YOUR_TOKEN
```

Proxies come from the **admin proxy pool** assigned to this worker in the panel.
Do not configure Telegram, billing, or users on the worker.
