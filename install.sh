#!/usr/bin/env bash
# Scrapeboard — macOS / Linux entry (calls install.py)
# Usage:  ./install.sh
#         bash install.sh --role worker --yes
#         SCRAPEBOARD_ASSUME_YES=1 bash install.sh --role panel
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "ERROR: Python 3.10+ not found. Install python3, then re-run."
  echo "  macOS: install Homebrew (https://brew.sh) then: brew install python@3.12"
  echo "  Debian/Ubuntu: sudo apt-get install -y python3 python3-venv python3-pip"
  exit 1
fi

exec "$PY" "$ROOT/install.py" "$@"
