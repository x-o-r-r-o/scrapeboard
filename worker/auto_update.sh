#!/usr/bin/env bash
# Scrapeboard worker — daily auto-update (check git → pip → restart service).
#
# Installed by install_service.sh as a companion timer/LaunchAgent.
# Manual:
#   bash worker/auto_update.sh
#   # or from repo root:
#   python3 install.py --role worker --auto-update --yes
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT"

mkdir -p "${SCRIPT_DIR}/logs"
LOG="${SCRIPT_DIR}/logs/auto_update.log"

{
  echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) worker auto-update ===="

  if [[ -f "${ROOT}/.scrapeboard-role" ]]; then
    ROLE="$(tr -d '[:space:]' <"${ROOT}/.scrapeboard-role" | head -1 | tr '[:upper:]' '[:lower:]')"
  elif [[ -n "${SCRAPEBOARD_ROLE:-}" ]]; then
    ROLE="$(printf '%s' "$SCRAPEBOARD_ROLE" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  else
    ROLE="worker"
  fi

  if [[ "$ROLE" == "panel" ]]; then
    echo "ERROR: this host is role=panel — use deploy/hestiacp/auto_update.sh"
    exit 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    PY=python3
  elif command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "ERROR: Python 3.10+ required"
    exit 1
  fi

  export SCRAPEBOARD_ASSUME_YES=1
  "$PY" "$ROOT/install.py" --role worker --auto-update --yes "$@"
} >>"$LOG" 2>&1
