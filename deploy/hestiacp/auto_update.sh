#!/usr/bin/env bash
# Scrapeboard panel — daily auto-update (check git → pull + install + restart).
#
# Invoked by systemd timer scrapeboard-auto-update.timer (installed by deploy).
# Safe to run manually as root:
#   bash deploy/hestiacp/auto_update.sh
#
set -euo pipefail

export CONTROL_PANEL=hestiacp

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../update.sh
# Prefer loading shared helpers via update.sh's sibling lib
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LIB="${ROOT}/deploy/lib/common.sh"

if [[ -f "${ROOT}/deploy/config.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/deploy/config.env"
fi

# shellcheck disable=SC1090
source "$LIB"

require_root
require_hestia
require_domain

assert_scrapeboard_role panel "$APP_DIR"

LOG_TAG="scrapeboard-auto-update"
echo "==> [${LOG_TAG}] $(date -u +%Y-%m-%dT%H:%M:%SZ) checking ${APP_DIR}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "==> [${LOG_TAG}] not a git checkout — skipping"
  exit 0
fi

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

echo "==> [${LOG_TAG}] git fetch"
sudo -u "${SITE_USER}" git -C "$APP_DIR" fetch origin --tags --prune

BRANCH="$(sudo -u "${SITE_USER}" git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
if [[ -z "$BRANCH" || "$BRANCH" == "HEAD" ]]; then
  BRANCH="main"
fi

HEAD="$(sudo -u "${SITE_USER}" git -C "$APP_DIR" rev-parse HEAD)"
REMOTE="$(sudo -u "${SITE_USER}" git -C "$APP_DIR" rev-parse "origin/${BRANCH}" 2>/dev/null || true)"
if [[ -z "$REMOTE" ]]; then
  REMOTE="$(sudo -u "${SITE_USER}" git -C "$APP_DIR" rev-parse origin/main 2>/dev/null || true)"
fi

if [[ -z "$REMOTE" ]]; then
  echo "==> [${LOG_TAG}] could not resolve origin tip — running full update"
elif [[ "$HEAD" == "$REMOTE" ]]; then
  echo "==> [${LOG_TAG}] already up to date (${HEAD:0:12}) — no restart"
  exit 0
else
  echo "==> [${LOG_TAG}] updates available: ${HEAD:0:12} → ${REMOTE:0:12}"
fi

# Full pull + pip + frontend build + systemd restart
run_update
echo "==> [${LOG_TAG}] complete"
