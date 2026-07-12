#!/usr/bin/env bash
# Scrapeboard worker — update this scrape host (role-based sparse git pull).
#
# Usage (from repo root or worker/):
#   bash worker/update.sh
#   bash worker/update.sh --force-role   # only if reconfiguring
#
# Requires .scrapeboard-role=worker (or SCRAPEBOARD_ROLE=worker).
# Does not pull panel/ or deploy/ onto this machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT"

if [[ -f "${ROOT}/.scrapeboard-role" ]]; then
  ROLE="$(tr -d '[:space:]' <"${ROOT}/.scrapeboard-role" | head -1 | tr '[:upper:]' '[:lower:]')"
elif [[ -n "${SCRAPEBOARD_ROLE:-}" ]]; then
  ROLE="$(printf '%s' "$SCRAPEBOARD_ROLE" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
else
  ROLE=""
fi

if [[ "$ROLE" == "panel" ]]; then
  cat >&2 <<EOF
ERROR: this machine is marked as panel (.scrapeboard-role or SCRAPEBOARD_ROLE).
Refusing worker update. On the panel VPS use:
  bash deploy/hestiacp/update.sh
To reconfigure this host as a worker:
  python3 install.py --role worker --force-role --update
EOF
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "ERROR: Python 3.10+ required for worker update." >&2
  exit 1
fi

exec "$PY" "$ROOT/install.py" --role worker --update "$@"
