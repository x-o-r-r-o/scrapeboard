#!/usr/bin/env bash
# Scrapeboard — macOS / Linux entry (calls install.py)
# Usage:  ./install.sh
#         bash install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "ERROR: Python 3.10+ not found. Install python3, then re-run."
  exit 1
fi

exec "$PY" "$ROOT/install.py" "$@"
