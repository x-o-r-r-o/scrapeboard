# Scrapeboard — Control Panel

FastAPI + React control panel. Source of truth for users, 2FA, reCAPTCHA, billing, proxy pools, workers, jobs, and Telegram Bot Builder.

## Production (HestiaCP) — preferred

Deploy once; runs as systemd until removed (same model as OpsBoard):

→ **[../../deploy/hestiacp/README.md](../../deploy/hestiacp/README.md)**

| | |
|--|--|
| Domain | `https://scrape.cvmso.com` |
| Hestia user | `cvmso` |
| API port | **3010** (OpsBoard 3000 / OmniDesk 3001) |
| Service | `systemctl status scrapeboard` |

Local `uvicorn` / `npm run dev` is for development only.

## Local development

### Backend

```bash
cd panel/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 3010
```

### Frontend

```bash
cd panel/frontend
npm install && npm run dev
```

## Worker

```bash
cd worker
python agent.py --panel-url https://scrape.cvmso.com --token YOUR_TOKEN
```
