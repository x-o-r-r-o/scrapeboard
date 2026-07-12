#!/usr/bin/env bash
# Scrapeboard worker — Linux / macOS first-run setup + selftest + start
# Usage:  bash setup_and_run.sh
set -u
cd "$(dirname "$0")" || exit 1

echo "================================================================"
echo " Scrapeboard Worker — setup ($(uname -s))"
echo "================================================================"
echo "Working dir: $(pwd)"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+."
  exit 1
fi
echo "Python: $(python3 --version)"

echo
echo "--- Creating virtual environment (.venv) ---"
python3 -m venv .venv || { echo "venv failed"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

echo "--- Installing requirements ---"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || { echo "pip install failed"; exit 1; }

echo "--- Playwright Chromium (first run) ---"
python -m playwright install chromium || true
# Linux system libs (best-effort)
if [[ "$(uname -s)" == "Linux" ]]; then
  python -m playwright install-deps chromium || true
fi

echo
echo "--- Selftest (chrome) ---"
python agent.py --selftest --engine chrome
echo "selftest exit: $?"

echo
echo "--- Starting worker (wizard if no worker_config.json) ---"
exec python agent.py
