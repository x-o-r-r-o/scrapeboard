#!/bin/bash
# Scrapeboard — macOS double-click entry (calls install.py via install.sh)
# Right-click → Open the first time if Gatekeeper blocks it.
set -euo pipefail
cd "$(dirname "$0")" || exit 1
exec bash "$(dirname "$0")/install.sh" "$@"
