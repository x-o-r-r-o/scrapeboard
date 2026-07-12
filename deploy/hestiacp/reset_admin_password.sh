#!/usr/bin/env bash
# Reset the bootstrap admin password in the live DB (does not recreate the user).
# Usage (as root on the VPS):
#   bash deploy/hestiacp/reset_admin_password.sh 'YourNewPasswordHere'
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -f "${ROOT}/deploy/config.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  # shellcheck disable=SC1090
  source "${ROOT}/deploy/config.env"
  set +a
fi

APP_DIR="${APP_DIR:-/home/cvmso/apps/scrapeboard}"
BACKEND_DIR="${APP_DIR}/panel/backend"
PYTHON="${BACKEND_DIR}/.venv/bin/python"
USER_NAME="${BOOTSTRAP_ADMIN_USERNAME:-admin}"
NEW_PASS="${1-}"

if [[ -z "$NEW_PASS" ]]; then
  echo "Usage: $0 'NewPassword'" >&2
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Python venv not found at $PYTHON" >&2
  exit 1
fi

export NEW_PASS USER_NAME
cd "$BACKEND_DIR"
sudo -u "${SITE_USER:-cvmso}" env NEW_PASS="$NEW_PASS" USER_NAME="$USER_NAME" "$PYTHON" - <<'PY'
import asyncio
import os
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import User

username = os.environ["USER_NAME"]
password = os.environ["NEW_PASS"]

async def main():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if not user:
            raise SystemExit(f"User {username!r} not found")
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.is_active = True
        await db.commit()
        print(f"Password reset for {username!r}. Log in, then change password + enable 2FA.")

asyncio.run(main())
PY
