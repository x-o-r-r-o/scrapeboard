#!/bin/bash
#
# macOS one-click setup + real test for the Google Maps scraper (Brave engine).
# Double-click this file in Finder, OR run:  bash mac_setup_and_test.command
#
# It will:
#   1. create a local Python virtual environment
#   2. install requirements + Playwright's Chromium driver
#   3. find your Brave browser
#   4. run the built-in --selftest (checks stealth + cache flush, no Google)
#   5. run a REAL 1-keyword / 1-city Brave scrape (direct connection, no proxies)
#   6. show you the resulting CSV
#
# Paste the whole output back to me if anything fails.

set -u
cd "$(dirname "$0")" || exit 1

echo "=================================================================="
echo " Google Maps Scraper — macOS setup & test (Brave)"
echo "=================================================================="
echo "Working dir: $(pwd)"
echo

# ---- 1. Python -----------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install it from https://www.python.org/downloads/"
  echo "Press any key to close."; read -n 1 -s; exit 1
fi
echo "Python: $(python3 --version)"

# ---- 2. venv + deps ------------------------------------------------------
echo
echo "--- Creating virtual environment (.venv) ---"
python3 -m venv .venv || { echo "venv creation failed"; read -n 1 -s; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

echo "--- Installing Python requirements (this can take a minute) ---"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || { echo "pip install failed"; read -n 1 -s; exit 1; }

echo "--- Installing Playwright Chromium driver (needed even for Brave) ---"
python -m playwright install chromium || { echo "playwright install failed"; read -n 1 -s; exit 1; }

# ---- 3. Detect Brave -----------------------------------------------------
echo
BRAVE="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
if [ -x "$BRAVE" ]; then
  echo "Brave found: $BRAVE"
else
  echo "WARNING: Brave not found at the standard location."
  echo "         The scraper will still try to auto-detect it."
fi

# ---- 4. Self-test (no Google) -------------------------------------------
echo
echo "=================================================================="
echo " STEP A: self-test (verifies Brave launches + stealth + cache flush)"
echo "=================================================================="
python gmaps_scraper.py --selftest --engine brave
SELFTEST_RC=$?
echo "self-test exit code: $SELFTEST_RC"

# ---- 5. Real scrape ------------------------------------------------------
echo
echo "=================================================================="
echo " STEP B: real scrape — 'coffee shop' in Austin, Texas (Brave, direct)"
echo "=================================================================="
python gmaps_scraper.py \
  --engine brave \
  --no-proxy \
  --threads 1 \
  --max-results 5 \
  --keywords test_keywords.txt \
  --locations test_locations.txt \
  --output test_results.csv
SCRAPE_RC=$?

# ---- 6. Show results -----------------------------------------------------
echo
echo "=================================================================="
echo " RESULTS"
echo "=================================================================="
if [ -f test_results.csv ]; then
  ROWS=$(( $(wc -l < test_results.csv) - 1 ))
  echo "test_results.csv rows: $ROWS"
  echo "----- first rows -----"
  head -n 6 test_results.csv
else
  echo "No CSV produced."
fi
echo
echo "self-test rc=$SELFTEST_RC  scrape rc=$SCRAPE_RC"
echo "Done. Press any key to close."
read -n 1 -s
