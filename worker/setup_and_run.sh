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

if [[ ! -f worker_config.json ]]; then
  echo
  echo "--- First-run wizard (creates worker_config.json) ---"
  python -c "from agent import bootstrap_agent_deps, run_setup_wizard; bootstrap_agent_deps(); run_setup_wizard()"
fi

if [[ -f worker_config.json ]]; then
  echo
  echo "================================================================"
  echo " Background service (recommended)"
  echo " Starts at login, keeps running after this terminal closes,"
  echo " and waits for panel jobs."
  echo "================================================================"
  echo "  Install:   bash install_service.sh"
  echo "  Uninstall: bash install_service.sh --uninstall"
  echo "  Logs:      logs/worker.log"
  echo
  if [[ -t 0 ]]; then
    read -r -p "Install background service now? [y/N] " ans || ans=""
    if [[ "${ans:-}" =~ ^[Yy] ]]; then
      bash install_service.sh
      echo
      echo "Service installed. Tail logs with:  tail -f logs/worker.log"
      exit 0
    fi
  else
    echo "Non-interactive shell — skip prompt. To install later:  bash install_service.sh"
  fi
fi

echo
echo "--- Starting worker in this terminal (Ctrl+C to stop) ---"
echo "Tip: later run  bash install_service.sh  for a login background service."
exec python agent.py
