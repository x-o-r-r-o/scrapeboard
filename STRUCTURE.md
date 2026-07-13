# Scrapeboard — Panel + Worker (multi-source)

| Folder | Role |
|--------|------|
| [`panel/`](panel/) | Scrapeboard web control panel (FastAPI + React) — [README](panel/README.md) |
| [`worker/`](worker/) | Worker scrape agent (Maps + Search + email + social) — [README](worker/README.md) |
| [`deploy/`](deploy/) | HestiaCP production install — [README](deploy/hestiacp/README.md) |
| [`TELEGRAM_USERS.md`](TELEGRAM_USERS.md) | Telegram end-user guide (all scrapers) |

**Machine role:** first install writes `.scrapeboard-role` (`panel` \| `worker`, gitignored) at the repo/app root. Override with `SCRAPEBOARD_ROLE`. Panel sync excludes `worker/`; worker sync excludes `panel/` and `deploy/`. Mismatched update commands fail unless you reconfigure (`--force-role` / `FORCE_ROLE_SWITCH=1`).

**Production:** deploy Scrapeboard once on HestiaCP (`scrape.cvmso.com`, systemd, port **3010**). Panel install/update **exclude `worker/`** (sparse-checkout); scrape agents are installed separately on worker hosts.

**Workers:** `setup_and_run.*` → wizard → **`install_service.*`** (default background service) → panel URL `https://scrape.cvmso.com`. Updates: `python3 install.py --role worker --update` or `bash worker/update.sh`. Optional Tailscale via wizard / `tailscale_enabled` in `worker_config.json` (default off).

**Start here:** `./install.sh` / `install.bat` / `python3 install.py` (panel vs worker by OS) — [README.md → Run by default](README.md#run-by-default).
