#!/usr/bin/env bash
# Scrapeboard — shared deploy helpers (HestiaCP)
set -euo pipefail

APP_NAME="scrapeboard"
SERVICE_NAME="scrapeboard"
API_PORT="${API_PORT:-3010}"
SITE_USER="${SITE_USER:-${HESTIA_USER:-cvmso}}"
HESTIA_USER="${HESTIA_USER:-$SITE_USER}"
DOMAIN="${DOMAIN:-scrape.cvmso.com}"
APP_DIR="${APP_DIR:-/home/${SITE_USER}/apps/${APP_NAME}}"
REPO_URL="${REPO_URL:-}"

PUBLIC_HTML="/home/${SITE_USER}/web/${DOMAIN}/public_html"
BACKEND_DIR="${APP_DIR}/panel/backend"
FRONTEND_DIR="${APP_DIR}/panel/frontend"
VENV="${BACKEND_DIR}/.venv"
PYTHON="${VENV}/bin/python"
UVICORN="${VENV}/bin/uvicorn"
NGINX_SNIPPET_SRC="${APP_DIR}/deploy/hestiacp/nginx.ssl.conf_scrapeboard"
NGINX_SNIPPET_DST="/home/${SITE_USER}/conf/web/${DOMAIN}/nginx.ssl.conf_scrapeboard"

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root (ssh root@server)." >&2
    exit 1
  fi
}

require_hestia() {
  if [[ ! -d /usr/local/hestia ]] && ! command -v v-rebuild-web-domain >/dev/null 2>&1; then
    echo "HestiaCP not detected." >&2
    exit 1
  fi
}

require_domain() {
  if [[ ! -d "$PUBLIC_HTML" ]]; then
    echo "Domain not found: $PUBLIC_HTML"
    echo "In HestiaCP: WEB → Add Web Domain → ${DOMAIN} (enable SSL / Let's Encrypt), then re-run."
    exit 1
  fi
}

install_system_packages() {
  echo "==> System packages..."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl git rsync ca-certificates openssl \
    python3 python3-venv python3-pip \
    build-essential
}

ensure_bun() {
  # Used only to build the React frontend (same approach as OpsBoard)
  if [[ -x /usr/local/bin/bun ]]; then
    return 0
  fi
  echo "==> Installing Bun (frontend build)..."
  if [[ ! -x /root/.bun/bin/bun ]]; then
    curl -fsSL https://bun.sh/install | bash
  fi
  install -m 755 /root/.bun/bin/bun /usr/local/bin/bun
}

sync_repo() {
  echo "==> App directory: $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  # Avoid "dubious ownership" when root runs install but files are owned by SITE_USER (or vice versa)
  if [[ -d "$APP_DIR" ]]; then
    chown -R "${SITE_USER}:${SITE_USER}" "$APP_DIR" || true
  fi
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

  if [[ -n "$REPO_URL" ]]; then
    if [[ ! -d "$APP_DIR/.git" ]]; then
      sudo -u "${SITE_USER}" git clone "$REPO_URL" "$APP_DIR"
    else
      sudo -u "${SITE_USER}" git -C "$APP_DIR" pull --ff-only
    fi
  else
    if [[ ! -d "$BACKEND_DIR" ]]; then
      echo "APP_DIR missing panel/backend. Either set REPO_URL or upload the project to:"
      echo "  $APP_DIR"
      exit 1
    fi
  fi
  chown -R "${SITE_USER}:${SITE_USER}" "$(dirname "$APP_DIR")"
}

env_quote() {
  # Safe dotenv double-quoted value (handles #, spaces, quotes, &, etc.)
  local v="${1-}"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//$'\n'/\\n}"
  v="${v//$'\r'/}"
  printf '"%s"' "$v"
}

write_backend_env() {
  echo "==> Writing panel/backend/.env ..."
  local secret="${SECRET_KEY:-}"
  if [[ -z "$secret" ]]; then
    if [[ -f "${BACKEND_DIR}/.env" ]] && grep -q '^SECRET_KEY=' "${BACKEND_DIR}/.env"; then
      # strip optional surrounding quotes from existing value
      secret="$(grep '^SECRET_KEY=' "${BACKEND_DIR}/.env" | head -1 | cut -d= -f2-)"
      secret="${secret#\"}"
      secret="${secret%\"}"
      secret="${secret#\'}"
      secret="${secret%\'}"
    else
      secret="$(openssl rand -hex 32)"
    fi
  fi

  local admin_user="${BOOTSTRAP_ADMIN_USERNAME:-admin}"
  local admin_email="${BOOTSTRAP_ADMIN_EMAIL:-admin@${DOMAIN}}"
  local admin_pass="${BOOTSTRAP_ADMIN_PASSWORD:-}"
  if [[ -z "$admin_pass" ]]; then
    admin_pass="$(openssl rand -base64 18 | tr -dc 'a-zA-Z0-9' | head -c 20)"
    echo "==> Generated BOOTSTRAP_ADMIN_PASSWORD (save it): ${admin_pass}"
  fi

  local data_dir="${APP_DIR}/panel/data"
  mkdir -p "$data_dir" "${data_dir}/uploads" "${data_dir}/results"
  chown -R "${SITE_USER}:${SITE_USER}" "${APP_DIR}/panel"

  local q_secret q_user q_email q_pass q_db q_cors q_url
  q_secret="$(env_quote "$secret")"
  q_user="$(env_quote "$admin_user")"
  q_email="$(env_quote "$admin_email")"
  q_pass="$(env_quote "$admin_pass")"
  q_db="$(env_quote "sqlite+aiosqlite:///${data_dir}/panel.db")"
  q_cors="$(env_quote "https://${DOMAIN},http://127.0.0.1:${API_PORT}")"
  q_url="$(env_quote "https://${DOMAIN}")"

  cat > "${BACKEND_DIR}/.env" <<EOF
APP_NAME=Scrapeboard
ENVIRONMENT=production
SECRET_KEY=${q_secret}
DATABASE_URL=${q_db}
CORS_ORIGINS=${q_cors}
ACCESS_TOKEN_EXPIRE_MINUTES=60
BOOTSTRAP_ADMIN_USERNAME=${q_user}
BOOTSTRAP_ADMIN_EMAIL=${q_email}
BOOTSTRAP_ADMIN_PASSWORD=${q_pass}
PUBLIC_URL=${q_url}
API_PORT=${API_PORT}
EOF
  chown "${SITE_USER}:${SITE_USER}" "${BACKEND_DIR}/.env"
  chmod 600 "${BACKEND_DIR}/.env"
}

install_backend() {
  echo "==> Python venv + deps..."
  if [[ ! -x "$PYTHON" ]]; then
    sudo -u "${SITE_USER}" python3 -m venv "$VENV"
  fi
  sudo -u "${SITE_USER}" "$VENV/bin/pip" install --upgrade pip
  sudo -u "${SITE_USER}" "$VENV/bin/pip" install -r "${BACKEND_DIR}/requirements.txt"
}

build_frontend() {
  echo "==> Building frontend..."
  cd "$FRONTEND_DIR"
  sudo -u "${SITE_USER}" /usr/local/bin/bun install
  sudo -u "${SITE_USER}" /usr/local/bin/bun run build
  if [[ ! -f "${FRONTEND_DIR}/dist/index.html" ]]; then
    echo "Frontend build failed: dist/index.html missing" >&2
    exit 1
  fi
}

publish_frontend() {
  echo "==> Publishing to ${PUBLIC_HTML} ..."
  if [[ ! -f "${FRONTEND_DIR}/dist/index.html" ]]; then
    echo "Nothing to publish: ${FRONTEND_DIR}/dist/index.html missing" >&2
    exit 1
  fi
  # Ensure Apache SPA rewrite is present even if Vite public/ was empty
  if [[ ! -f "${FRONTEND_DIR}/dist/.htaccess" ]] && [[ -f "${FRONTEND_DIR}/public/.htaccess" ]]; then
    cp "${FRONTEND_DIR}/public/.htaccess" "${FRONTEND_DIR}/dist/.htaccess"
  fi
  rsync -a --delete \
    --exclude '.htaccess' \
    "${FRONTEND_DIR}/dist/" "${PUBLIC_HTML}/"
  # Always keep SPA fallback files
  if [[ -f "${FRONTEND_DIR}/dist/.htaccess" ]]; then
    cp -f "${FRONTEND_DIR}/dist/.htaccess" "${PUBLIC_HTML}/.htaccess"
  elif [[ -f "${FRONTEND_DIR}/public/.htaccess" ]]; then
    cp -f "${FRONTEND_DIR}/public/.htaccess" "${PUBLIC_HTML}/.htaccess"
  fi
  chown -R "${SITE_USER}:${SITE_USER}" "${PUBLIC_HTML}"
  if [[ ! -f "${PUBLIC_HTML}/index.html" ]]; then
    echo "Publish failed: ${PUBLIC_HTML}/index.html missing" >&2
    exit 1
  fi
  echo "    Published index.html + assets to ${PUBLIC_HTML}"
}

install_spa_web_template() {
  # Optional: Hestia nginx template with try_files → /index.html for React Router
  echo "==> Ensuring scrapeboard SPA web template (if Hestia templates exist)..."
  local dir ext src
  for dir in \
    /usr/local/hestia/data/templates/web/nginx \
    /usr/local/hestia/data/templates/web/nginx/php-fpm
  do
    [[ -d "$dir" ]] || continue
    for ext in tpl stpl; do
      src="$dir/default.${ext}"
      [[ -f "$src" ]] || continue
      cp -f "$src" "$dir/scrapeboard.${ext}"
      # Common Hestia try_files lines → SPA fallback
      sed -i \
        -e 's|try_files \$uri \$uri/ =404;|try_files $uri $uri/ /index.html;|g' \
        -e 's|try_files \$uri \$uri/ /index.php?\$query_string;|try_files $uri $uri/ /index.html;|g' \
        -e 's|try_files \$uri \$uri/ /index.php?\$args;|try_files $uri $uri/ /index.html;|g' \
        "$dir/scrapeboard.${ext}" || true
    done
  done
  # Apply template when the CLI supports it (ignore failures on mixed stacks)
  if command -v v-change-web-domain-tpl >/dev/null 2>&1; then
    v-change-web-domain-tpl "${SITE_USER}" "${DOMAIN}" scrapeboard 2>/dev/null \
      || echo "    (hint) In HestiaCP → WEB → edit ${DOMAIN} → set Nginx template to scrapeboard"
  fi
}

write_systemd() {
  echo "==> systemd unit ${SERVICE_NAME}.service ..."
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Scrapeboard API (FastAPI)
After=network.target

[Service]
Type=simple
User=${SITE_USER}
Group=${SITE_USER}
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=${BACKEND_DIR}/.env
Environment=PATH=${VENV}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${UVICORN} app.main:app --host 127.0.0.1 --port ${API_PORT} --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
}

install_nginx_snippet() {
  echo "==> HestiaCP nginx snippet..."
  # Ensure port in snippet matches API_PORT
  local tmp
  tmp="$(mktemp)"
  sed "s/127.0.0.1:3010/127.0.0.1:${API_PORT}/g" "$NGINX_SNIPPET_SRC" > "$tmp"
  install -m 644 "$tmp" "$NGINX_SNIPPET_DST"
  rm -f "$tmp"
  chown "${SITE_USER}:${SITE_USER}" "$NGINX_SNIPPET_DST"
  v-rebuild-web-domain "${SITE_USER}" "${DOMAIN}"
}

wait_health() {
  echo "==> Health check..."
  local i
  for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${API_PORT}/api/health" >/dev/null; then
      echo "    API OK on 127.0.0.1:${API_PORT}"
      return 0
    fi
    sleep 1
  done
  echo "API did not become healthy. Logs:"
  journalctl -u "${SERVICE_NAME}" -n 50 --no-pager || true
  return 1
}

print_done() {
  cat <<EOF

================================================================
 Scrapeboard is installed and running as a service.
================================================================
 URL:      https://${DOMAIN}
 API:      127.0.0.1:${API_PORT}  (systemd: ${SERVICE_NAME})
 App dir:  ${APP_DIR}
 Workers:  python agent.py --panel-url https://${DOMAIN} --token TOKEN

 Manage:
   systemctl status ${SERVICE_NAME}
   journalctl -u ${SERVICE_NAME} -f
   systemctl restart ${SERVICE_NAME}

 Update after code changes:
   SITE_USER=${SITE_USER} DOMAIN=${DOMAIN} bash deploy/hestiacp/update.sh
================================================================
EOF
}

run_install() {
  require_root
  require_hestia
  require_domain
  install_system_packages
  ensure_bun
  sync_repo
  write_backend_env
  install_backend
  build_frontend
  publish_frontend
  write_systemd
  install_nginx_snippet
  install_spa_web_template
  wait_health
  print_done
}

run_update() {
  require_root
  require_hestia
  require_domain
  sync_repo
  # keep existing .env (do not overwrite secrets)
  if [[ ! -f "${BACKEND_DIR}/.env" ]]; then
    write_backend_env
  fi
  install_backend
  build_frontend
  publish_frontend
  write_systemd
  install_nginx_snippet
  install_spa_web_template
  wait_health
  echo "==> Update complete: https://${DOMAIN}"
}
