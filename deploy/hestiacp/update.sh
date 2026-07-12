#!/usr/bin/env bash
# Wrapper — HestiaCP update for Scrapeboard
set -euo pipefail
export CONTROL_PANEL=hestiacp
exec "$(dirname "$0")/../update.sh" "$@"
