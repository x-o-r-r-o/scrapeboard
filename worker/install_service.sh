#!/usr/bin/env bash
# Install Scrapeboard worker as a background service that starts on boot/login
# and keeps running after the terminal closes.
#
# Also installs a daily auto-update timer (git check → pip → service restart).
#
# Usage (from the worker directory, after wizard / worker_config.json exists):
#   bash install_service.sh
#   bash install_service.sh --uninstall
#   bash install_service.sh --no-auto-update   # service only
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
OS="$(uname -s)"
NAME="scrapeboard-worker"
AUTO_NAME="scrapeboard-worker-auto-update"
PYTHON=""
UNINSTALL=0
INSTALL_AUTO_UPDATE=1
AUTO_HOUR="${SCRAPEBOARD_AUTO_UPDATE_HOUR:-4}"
AUTO_MINUTE="${SCRAPEBOARD_AUTO_UPDATE_MINUTE:-0}"

for arg in "$@"; do
  case "$arg" in
    --uninstall|-u) UNINSTALL=1 ;;
    --no-auto-update) INSTALL_AUTO_UPDATE=0 ;;
    -h|--help)
      echo "Usage: bash install_service.sh [--uninstall] [--no-auto-update]"
      exit 0
      ;;
  esac
done

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "ERROR: Python not found. Run setup_and_run.sh first."
  exit 1
fi

if [[ ! -f "$ROOT/worker_config.json" && "$UNINSTALL" -eq 0 ]]; then
  echo "ERROR: worker_config.json missing."
  echo "Run first:  $PYTHON agent.py --setup"
  exit 1
fi

mkdir -p "$ROOT/logs" "$ROOT/work"
chmod +x "$ROOT/auto_update.sh" 2>/dev/null || true

# Clamp schedule
if ! [[ "$AUTO_HOUR" =~ ^[0-9]+$ ]] || (( AUTO_HOUR > 23 )); then AUTO_HOUR=4; fi
if ! [[ "$AUTO_MINUTE" =~ ^[0-9]+$ ]] || (( AUTO_MINUTE > 59 )); then AUTO_MINUTE=0; fi

install_macos_auto_update() {
  local plist="$HOME/Library/LaunchAgents/com.scrapeboard.worker.auto-update.plist"
  if [[ "$UNINSTALL" -eq 1 || "$INSTALL_AUTO_UPDATE" -eq 0 ]]; then
    launchctl bootout "gui/$(id -u)/com.scrapeboard.worker.auto-update" 2>/dev/null || true
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "Removed auto-update LaunchAgent (if present)."
    return
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.scrapeboard.worker.auto-update</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${ROOT}/auto_update.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO_ROOT}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${AUTO_HOUR}</integer>
    <key>Minute</key>
    <integer>${AUTO_MINUTE}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/auto_update.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/auto_update.launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    <key>SCRAPEBOARD_ASSUME_YES</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/com.scrapeboard.worker.auto-update" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || launchctl load -w "$plist"
  echo "Installed daily auto-update LaunchAgent at ${AUTO_HOUR}:$(printf '%02d' "$AUTO_MINUTE") (local)."
  echo "  plist: $plist"
  echo "  logs:  $ROOT/logs/auto_update.log"
}

install_linux_auto_update() {
  local unit_dir="$HOME/.config/systemd/user"
  local unit="$unit_dir/${AUTO_NAME}.service"
  local timer="$unit_dir/${AUTO_NAME}.timer"
  if [[ "$UNINSTALL" -eq 1 || "$INSTALL_AUTO_UPDATE" -eq 0 ]]; then
    systemctl --user disable --now "$AUTO_NAME.timer" 2>/dev/null || true
    rm -f "$unit" "$timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed auto-update systemd timer (if present)."
    return
  fi
  mkdir -p "$unit_dir"
  printf -v hour "%02d" "$AUTO_HOUR"
  printf -v minute "%02d" "$AUTO_MINUTE"
  cat > "$unit" <<EOF
[Unit]
Description=Scrapeboard worker git auto-update (check → pip → restart)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
Nice=10
Environment=SCRAPEBOARD_ASSUME_YES=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/bin/bash ${ROOT}/auto_update.sh
EOF
  cat > "$timer" <<EOF
[Unit]
Description=Daily Scrapeboard worker auto-update check

[Timer]
OnCalendar=*-*-* ${hour}:${minute}:00
Persistent=true
RandomizedDelaySec=900
Unit=${AUTO_NAME}.service

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$AUTO_NAME.timer"
  echo "Installed daily auto-update timer at ${hour}:${minute} (local)."
  echo "  timer: systemctl --user list-timers ${AUTO_NAME}.timer"
  echo "  logs:  $ROOT/logs/auto_update.log"
}

install_macos() {
  local plist="$HOME/Library/LaunchAgents/com.scrapeboard.worker.plist"
  if [[ "$UNINSTALL" -eq 1 ]]; then
    launchctl bootout "gui/$(id -u)/com.scrapeboard.worker" 2>/dev/null || true
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "Uninstalled LaunchAgent: $plist"
    install_macos_auto_update
    return
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.scrapeboard.worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/agent.py</string>
    <string>--service</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/com.scrapeboard.worker" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || launchctl load -w "$plist"
  launchctl kickstart -k "gui/$(id -u)/com.scrapeboard.worker" 2>/dev/null || true
  echo "Installed macOS LaunchAgent (starts at login, KeepAlive)."
  echo "  plist: $plist"
  echo "  logs:  $ROOT/logs/worker.log"
  echo "  status: launchctl print gui/$(id -u)/com.scrapeboard.worker | head"
  install_macos_auto_update
}

install_linux() {
  local unit_dir="$HOME/.config/systemd/user"
  local unit="$unit_dir/${NAME}.service"
  if [[ "$UNINSTALL" -eq 1 ]]; then
    systemctl --user disable --now "$NAME" 2>/dev/null || true
    rm -f "$unit"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Uninstalled systemd user unit: $unit"
    install_linux_auto_update
    return
  fi
  mkdir -p "$unit_dir"
  cat > "$unit" <<EOF
[Unit]
Description=Scrapeboard scrape worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT}
ExecStart=${PYTHON} ${ROOT}/agent.py --service
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=60
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$NAME"
  # Survive logout (best-effort; may need sudo once)
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$(id -un)" 2>/dev/null || \
      echo "NOTE: run once for boot without login:  sudo loginctl enable-linger $(id -un)"
  fi
  echo "Installed systemd user service (starts at login/boot with linger)."
  echo "  unit:   $unit"
  echo "  logs:   $ROOT/logs/worker.log  (also: journalctl --user -u $NAME -f)"
  echo "  status: systemctl --user status $NAME"
  install_linux_auto_update
}

case "$OS" in
  Darwin) install_macos ;;
  Linux) install_linux ;;
  *)
    echo "Unsupported OS: $OS"
    echo "On Windows use:  install_service.bat"
    exit 1
    ;;
esac
