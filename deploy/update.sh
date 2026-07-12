#!/usr/bin/env bash
# Scrapeboard — update deployment (run as root)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "${SCRIPT_DIR}/config.env" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/config.env"
fi

SITE_USER="${SITE_USER:-${HESTIA_USER:-cvmso}}"
HESTIA_USER="${HESTIA_USER:-$SITE_USER}"
DOMAIN="${DOMAIN:-scrape.cvmso.com}"
API_PORT="${API_PORT:-3010}"
APP_DIR="${APP_DIR:-/home/${SITE_USER}/apps/scrapeboard}"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
run_update
