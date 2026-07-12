#!/usr/bin/env bash
# Scrapeboard panel API — local / day-to-day start (uvicorn on :3010)
# Usage:  bash panel/run.sh
#         bash panel/run.sh --reload    # development hot-reload
set -euo pipefail
cd "$(dirname "$0")/backend" || exit 1

if [[ ! -d .venv ]]; then
  echo "Creating venv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD, then re-run."
  exit 1
fi

python -m pip install -q -r requirements.txt

RELOAD=()
if [[ "${1:-}" == "--reload" ]]; then
  RELOAD=(--reload)
fi

PORT="${API_PORT:-3010}"
echo "Scrapeboard API → http://127.0.0.1:${PORT}  (Telegram bot starts with this process)"
exec uvicorn app.main:app --host 127.0.0.1 --port "$PORT" "${RELOAD[@]}"
