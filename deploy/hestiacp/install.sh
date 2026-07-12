#!/usr/bin/env bash
# Wrapper — HestiaCP install for Scrapeboard
set -euo pipefail
export CONTROL_PANEL=hestiacp
exec "$(dirname "$0")/../install.sh" "$@"
