#!/bin/bash
# Scrapeboard worker — macOS double-click setup + selftest + start
# Right-click → Open the first time if Gatekeeper blocks it.
set -u
cd "$(dirname "$0")" || exit 1
exec bash "$(dirname "$0")/setup_and_run.sh"
