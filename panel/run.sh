#!/usr/bin/env bash
# Scrapeboard panel API — local / day-to-day start (uvicorn on :3010)
# Usage:  bash panel/run.sh
#         bash panel/run.sh --reload    # development hot-reload
# With SCRAPEBOARD_ASSUME_YES=1, missing .env is created with generated secrets.
set -euo pipefail
cd "$(dirname "$0")/backend" || exit 1

ASSUME_YES=0
case "${SCRAPEBOARD_ASSUME_YES:-}" in
  1|true|TRUE|yes|YES|y|Y|on|ON) ASSUME_YES=1 ;;
esac

if [[ ! -d .venv ]]; then
  echo "Creating venv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
  if [[ ! -f .env.example ]]; then
    echo "ERROR: missing .env.example"
    exit 1
  fi
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    ADMIN_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
    # shellcheck disable=SC2002
    sed \
      -e "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" \
      -e "s|^BOOTSTRAP_ADMIN_PASSWORD=.*|BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASS}|" \
      .env.example > .env
    echo "Created .env with generated secrets (SCRAPEBOARD_ASSUME_YES=1)."
    echo "  BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASS}  (save this)"
  else
    cp .env.example .env
    echo "Created .env from .env.example — edit SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD, then re-run."
    exit 1
  fi
fi

python -m pip install -q -r requirements.txt

RELOAD=()
if [[ "${1:-}" == "--reload" ]]; then
  RELOAD=(--reload)
fi

PORT="${API_PORT:-3010}"
echo "Scrapeboard API → http://127.0.0.1:${PORT}  (Telegram bot starts with this process)"
exec uvicorn app.main:app --host 127.0.0.1 --port "$PORT" "${RELOAD[@]}"
